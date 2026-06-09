"""nixx-image: on-demand image generation and editing service.

Generation uses FLUX.1 Schnell (fast, ~1 min, Apache 2.0).
Editing uses FLUX.1 Kontext [dev] (slower, ~25 min, supports input images).

Runs on localhost:8090 only. Started on demand by the ImageTool in nixx.
Shuts itself down after IDLE_TIMEOUT seconds with no requests.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import torch
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

logger = logging.getLogger("nixx-image")

SCHNELL_MODEL_ID = "black-forest-labs/FLUX.1-schnell"
KONTEXT_MODEL_ID = "black-forest-labs/FLUX.1-Kontext-dev"
IDLE_TIMEOUT = int(os.environ.get("NIXX_IMAGE_IDLE_TIMEOUT", "600"))  # 10 min default
OUTPUT_DIR = Path(os.environ.get("NIXX_IMAGE_OUTPUT_DIR", Path.home() / "nixx_scratch" / "images"))
JOB_LOG = OUTPUT_DIR.parent / "image_jobs.jsonl"  # persistent record of completed jobs


def _append_job_log(job_id: str, job: dict[str, Any]) -> None:
    """Append a completed job record to the persistent JSONL log."""
    record = {
        "job_id": job_id,
        "type": job.get("type"),
        "status": job["status"],
        "submitted_at": job.get("submitted_at"),
        "completed_at": job.get("completed_at"),
        "latency_ms": job.get("latency_ms"),
    }
    try:
        JOB_LOG.parent.mkdir(parents=True, exist_ok=True)
        with JOB_LOG.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        logger.warning("Failed to write job log", exc_info=True)


class GenerateRequest(BaseModel):
    prompt: str
    width: int = 1024
    height: int = 1024
    steps: int = 4  # Schnell is distilled - 4 steps gives good quality


class EditRequest(BaseModel):
    prompt: str
    input_path: str
    # 768×768 max on GTX 1070 (8 GB). At 1024×1024, Kontext's concatenated
    # reference+target tokens produce a ~6.77 GiB attention allocation that
    # doesn't fit. 768×768 brings this down to ~2.2 GiB.
    width: int = 768
    height: int = 768
    steps: int = 28  # Kontext needs more steps for quality edits

# ── Global state ───────────────────────────────────────────────────────────────

_schnell_pipe: Any = None  # FluxPipeline, loaded lazily
_schnell_lock = threading.Lock()
_kontext_pipe: Any = None  # FluxKontextPipeline, loaded lazily
_kontext_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}  # job_id -> {status, result, error}
_last_request: float = time.monotonic()


def _unload_schnell() -> None:
    global _schnell_pipe
    if _schnell_pipe is None:
        return
    with _schnell_lock:
        _schnell_pipe = None
    torch.cuda.empty_cache()
    logger.info("FLUX.1 Schnell unloaded.")


def _unload_kontext() -> None:
    global _kontext_pipe
    if _kontext_pipe is None:
        return
    with _kontext_lock:
        _kontext_pipe = None
    torch.cuda.empty_cache()
    logger.info("FLUX.1 Kontext [dev] unloaded.")


def _load_schnell() -> Any:
    global _schnell_pipe
    if _schnell_pipe is not None:
        return _schnell_pipe
    _unload_kontext()  # Free VRAM before loading
    with _schnell_lock:
        if _schnell_pipe is not None:
            return _schnell_pipe
        logger.info("Loading FLUX.1 Schnell...")
        from diffusers import FluxPipeline

        p = FluxPipeline.from_pretrained(SCHNELL_MODEL_ID, torch_dtype=torch.bfloat16)
        p.to("cuda")
        p.vae.enable_slicing()
        p.vae.enable_tiling()
        _schnell_pipe = p
        logger.info("FLUX.1 Schnell loaded.")
        return _schnell_pipe


def _load_kontext() -> Any:
    global _kontext_pipe
    if _kontext_pipe is not None:
        return _kontext_pipe
    _unload_schnell()  # Free VRAM before loading
    with _kontext_lock:
        if _kontext_pipe is not None:
            return _kontext_pipe
        logger.info("Loading FLUX.1 Kontext [dev]...")
        from diffusers import FluxKontextPipeline
        from transformers import T5EncoderModel

        hf_token = os.environ.get("HF_READ_TOKEN")
        # Run Kontext entirely on CPU. The Flux transformer's double-stream
        # attention allocates ~6.77 GiB in a single tensor - larger than the
        # GTX 1070's 8 GB VRAM. By leaving all components on CPU (default for
        # from_pretrained without device_map or .to("cuda")), the 64 GB RAM
        # absorbs it. Slow (~2-3 hours) but correct.
        text_encoder_2 = T5EncoderModel.from_pretrained(
            KONTEXT_MODEL_ID,
            subfolder="text_encoder_2",
            torch_dtype=torch.bfloat16,
            token=hf_token,
        )
        p = FluxKontextPipeline.from_pretrained(
            KONTEXT_MODEL_ID,
            text_encoder_2=text_encoder_2,
            torch_dtype=torch.bfloat16,
            token=hf_token,
        )
        p.vae.enable_slicing()
        p.vae.enable_tiling()
        _kontext_pipe = p
        logger.info("FLUX.1 Kontext [dev] loaded (CPU mode).")
        return _kontext_pipe


def _run_generate(job_id: str, prompt: str, width: int, height: int, steps: int) -> None:
    global _last_request
    try:
        pipe = _load_schnell()
        _last_request = time.monotonic()
        logger.info("Generating image for job %s", job_id)
        result = pipe(
            prompt=prompt,
            width=width,
            height=height,
            num_inference_steps=steps,
            guidance_scale=3.5,
        )
        img = result.images[0]
        out_path = OUTPUT_DIR / f"{job_id}.png"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path)
        _jobs[job_id]["status"] = "done"
        _jobs[job_id]["result"] = str(out_path)
        _jobs[job_id]["completed_at"] = time.time()
        _jobs[job_id]["latency_ms"] = int((_jobs[job_id]["completed_at"] - _jobs[job_id]["submitted_at"]) * 1000)
        logger.info("Job %s done: %s", job_id, out_path)
        _append_job_log(job_id, _jobs[job_id])
    except Exception as exc:
        logger.exception("Job %s failed", job_id)
        _jobs[job_id]["status"] = "error"
        _jobs[job_id]["error"] = str(exc)
        _jobs[job_id]["completed_at"] = time.time()
        _append_job_log(job_id, _jobs[job_id])
    finally:
        _last_request = time.monotonic()


def _run_edit(
    job_id: str,
    prompt: str,
    input_path: str,
    width: int,
    height: int,
    steps: int,
) -> None:
    global _last_request
    try:
        from PIL import Image

        pipe = _load_kontext()
        _last_request = time.monotonic()
        logger.info("Editing image for job %s", job_id)
        input_image = Image.open(input_path).convert("RGB")
        result = pipe(
            prompt=prompt,
            image=input_image,
            width=width,
            height=height,
            num_inference_steps=steps,
            guidance_scale=3.5,
        )
        img = result.images[0]
        out_path = OUTPUT_DIR / f"{job_id}.png"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path)
        _jobs[job_id]["status"] = "done"
        _jobs[job_id]["result"] = str(out_path)
        _jobs[job_id]["completed_at"] = time.time()
        _jobs[job_id]["latency_ms"] = int((_jobs[job_id]["completed_at"] - _jobs[job_id]["submitted_at"]) * 1000)
        logger.info("Job %s done: %s", job_id, out_path)
        _append_job_log(job_id, _jobs[job_id])
    except Exception as exc:
        logger.exception("Job %s failed", job_id)
        _jobs[job_id]["status"] = "error"
        _jobs[job_id]["error"] = str(exc)
        _jobs[job_id]["completed_at"] = time.time()
        _append_job_log(job_id, _jobs[job_id])
    finally:
        _last_request = time.monotonic()


# ── FastAPI app ────────────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    app = FastAPI(title="nixx-image", docs_url=None, redoc_url=None)

    @app.on_event("startup")
    async def _start_idle_watcher() -> None:
        asyncio.create_task(_idle_watcher())

    @app.get("/health")
    async def health() -> dict[str, Any]:
        global _last_request
        _last_request = time.monotonic()
        return {
            "status": "ok",
            "generate_model": SCHNELL_MODEL_ID,
            "edit_model": KONTEXT_MODEL_ID,
            "schnell_loaded": _schnell_pipe is not None,
            "kontext_loaded": _kontext_pipe is not None,
        }

    @app.post("/generate")
    async def generate(req: GenerateRequest) -> dict[str, str]:
        global _last_request
        _last_request = time.monotonic()
        job_id = uuid.uuid4().hex
        _jobs[job_id] = {"status": "running", "type": "generate", "result": None, "error": None, "submitted_at": time.time(), "completed_at": None, "latency_ms": None}
        t = threading.Thread(
            target=_run_generate,
            args=(job_id, req.prompt, req.width, req.height, req.steps),
            daemon=True,
        )
        t.start()
        return {"job_id": job_id, "status": "running"}

    @app.post("/edit")
    async def edit(req: EditRequest) -> dict[str, str]:
        global _last_request
        _last_request = time.monotonic()
        if not Path(req.input_path).exists():
            raise HTTPException(status_code=400, detail=f"Input file not found: {req.input_path}")
        job_id = uuid.uuid4().hex
        _jobs[job_id] = {"status": "running", "type": "edit", "result": None, "error": None, "submitted_at": time.time(), "completed_at": None, "latency_ms": None}
        t = threading.Thread(
            target=_run_edit,
            args=(job_id, req.prompt, req.input_path, req.width, req.height, req.steps),
            daemon=True,
        )
        t.start()
        return {"job_id": job_id, "status": "running"}

    @app.get("/status/{job_id}")
    async def status(job_id: str) -> dict[str, Any]:
        if job_id not in _jobs:
            raise HTTPException(status_code=404, detail="Unknown job")
        return _jobs[job_id]

    @app.get("/jobs")
    async def list_jobs() -> dict[str, Any]:
        jobs = [
            {
                "job_id": jid,
                "status": j["status"],
                "type": j.get("type"),
                "submitted_at": j.get("submitted_at"),
                "completed_at": j.get("completed_at"),
                "latency_ms": j.get("latency_ms"),
            }
            for jid, j in _jobs.items()
        ]
        return {"jobs": jobs, "total": len(jobs), "done": sum(1 for j in _jobs.values() if j["status"] == "done")}

    @app.get("/download/{job_id}")
    async def download(job_id: str) -> FileResponse:
        if job_id not in _jobs:
            raise HTTPException(status_code=404, detail="Unknown job")
        job = _jobs[job_id]
        if job["status"] != "done":
            raise HTTPException(status_code=425, detail=f"Job status: {job['status']}")
        return FileResponse(job["result"], media_type="image/png", filename=f"{job_id}.png")

    return app


async def _idle_watcher() -> None:
    """Shut down the service after IDLE_TIMEOUT seconds with no requests."""
    while True:
        await asyncio.sleep(60)
        # Never shut down while a job is actively running.
        if any(j["status"] == "running" for j in _jobs.values()):
            continue
        idle = time.monotonic() - _last_request
        if idle >= IDLE_TIMEOUT:
            logger.info("Idle timeout reached (%.0fs). Shutting down.", idle)
            os.kill(os.getpid(), signal.SIGTERM)


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    uvicorn.run(
        "nixx.image_service.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=8090,
        log_level="info",
    )


if __name__ == "__main__":
    main()
