"""nixx-image: on-demand image generation and editing service.

Generation uses FLUX.1 Schnell (fast, ~30s, Apache 2.0).
Editing uses either:
  - InstructPix2Pix (fast, ~1-2 min on GTX 1070, GPU) [default]
  - FLUX.1 Kontext [dev] (slow, ~2-3 hours, CPU-only due to VRAM constraints)

Active edit model is controlled at runtime via GET/POST /edit_model.

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
IP2P_MODEL_ID = "timbrooks/instruct-pix2pix"
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
    width: int = 512   # SD 3.5 Medium default; Kontext also accepts this
    height: int = 512
    steps: int = 28

# ── Global state ───────────────────────────────────────────────────────────────

_schnell_pipe: Any = None  # FluxPipeline, loaded lazily
_schnell_lock = threading.Lock()
_kontext_pipe: Any = None  # FluxKontextPipeline, loaded lazily (CPU-only)
_kontext_lock = threading.Lock()
_ip2p_pipe: Any = None  # StableDiffusionInstructPix2PixPipeline, loaded lazily (GPU)
_ip2p_lock = threading.Lock()
_active_edit_model: str = "ip2p"  # "ip2p" or "kontext"
_edit_model_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}  # job_id -> {status, result, error}
_last_request: float = time.monotonic()


def _unload_schnell() -> None:
    global _schnell_pipe
    if _schnell_pipe is None:
        return
    with _schnell_lock:
        _schnell_pipe = None
    import gc
    gc.collect()
    torch.cuda.empty_cache()
    logger.info("FLUX.1 Schnell unloaded.")


def _unload_kontext() -> None:
    global _kontext_pipe
    if _kontext_pipe is None:
        return
    with _kontext_lock:
        _kontext_pipe = None
    import gc
    gc.collect()
    torch.cuda.empty_cache()
    logger.info("FLUX.1 Kontext [dev] unloaded.")


def _unload_ip2p() -> None:
    global _ip2p_pipe
    if _ip2p_pipe is None:
        return
    with _ip2p_lock:
        _ip2p_pipe = None
    import gc
    gc.collect()
    torch.cuda.empty_cache()
    logger.info("InstructPix2Pix unloaded.")


def _load_schnell() -> Any:
    global _schnell_pipe
    if _schnell_pipe is not None:
        return _schnell_pipe
    _unload_kontext()  # Free RAM
    _unload_ip2p()    # Free VRAM (both use GPU)
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


def _load_ip2p() -> Any:
    global _ip2p_pipe
    if _ip2p_pipe is not None:
        return _ip2p_pipe
    _unload_schnell()  # Free VRAM (both use GPU)
    _unload_kontext()  # Free RAM
    with _ip2p_lock:
        if _ip2p_pipe is not None:
            return _ip2p_pipe
        logger.info("Loading InstructPix2Pix...")
        from diffusers import StableDiffusionInstructPix2PixPipeline

        p = StableDiffusionInstructPix2PixPipeline.from_pretrained(
            IP2P_MODEL_ID,
            torch_dtype=torch.float16,
            safety_checker=None,
        )
        p.to("cuda")
        _ip2p_pipe = p
        logger.info("InstructPix2Pix loaded.")
        return _ip2p_pipe


def _load_kontext() -> Any:
    global _kontext_pipe
    if _kontext_pipe is not None:
        return _kontext_pipe
    _unload_schnell()  # Free VRAM
    _unload_ip2p()     # Free VRAM
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


def _run_edit_ip2p(
    job_id: str,
    prompt: str,
    input_path: str,
    width: int,
    height: int,
) -> None:
    global _last_request
    try:
        from PIL import Image, ImageOps

        pipe = _load_ip2p()
        _last_request = time.monotonic()
        logger.info("Editing image (InstructPix2Pix) for job %s", job_id)
        # IP2P works best with images resized to multiples of 8
        w = (width // 8) * 8
        h = (height // 8) * 8
        input_image = ImageOps.exif_transpose(Image.open(input_path).convert("RGB")).resize((w, h))
        result = pipe(
            prompt=prompt,
            image=input_image,
            num_inference_steps=50,
            image_guidance_scale=1.5,  # how much to preserve original
            guidance_scale=7.5,        # how strongly to follow text prompt
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


def _run_edit_kontext(
    job_id: str,
    prompt: str,
    input_path: str,
    width: int,
    height: int,
    steps: int,
) -> None:
    global _last_request
    try:
        from PIL import Image, ImageOps

        # Hide all GPUs during Kontext inference. Even with no explicit .to("cuda"),
        # diffusers moves tensors to the available CUDA device internally at call time.
        # Setting CUDA_VISIBLE_DEVICES="" forces everything onto CPU.
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
        pipe = _load_kontext()
        _last_request = time.monotonic()
        logger.info("Editing image (Kontext) for job %s", job_id)
        input_image = ImageOps.exif_transpose(Image.open(input_path).convert("RGB"))
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
        # Restore GPU visibility so Schnell/SD35 can use CUDA if needed later
        os.environ["CUDA_VISIBLE_DEVICES"] = "1"
        _last_request = time.monotonic()


def _run_edit(
    job_id: str,
    prompt: str,
    input_path: str,
    width: int,
    height: int,
    steps: int,
) -> None:
    """Dispatch to the active edit model."""
    if _active_edit_model == "kontext":
        _run_edit_kontext(job_id, prompt, input_path, width, height, steps)
    else:
        _run_edit_ip2p(job_id, prompt, input_path, width, height)


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
            "active_edit_model": _active_edit_model,
            "edit_model_id": IP2P_MODEL_ID if _active_edit_model == "ip2p" else KONTEXT_MODEL_ID,
            "schnell_loaded": _schnell_pipe is not None,
            "ip2p_loaded": _ip2p_pipe is not None,
            "kontext_loaded": _kontext_pipe is not None,
        }

    @app.get("/edit_model")
    async def get_edit_model() -> dict[str, str]:
        return {"model": _active_edit_model}

    @app.post("/edit_model")
    async def set_edit_model(body: dict[str, str]) -> dict[str, str]:
        global _active_edit_model
        model = body.get("model", "").lower()
        if model not in ("ip2p", "kontext"):
            raise HTTPException(status_code=400, detail="model must be 'ip2p' or 'kontext'")
        with _edit_model_lock:
            _active_edit_model = model
        logger.info("Active edit model set to: %s", model)
        labels = {"ip2p": "InstructPix2Pix (fast, GPU)", "kontext": "FLUX.1 Kontext [dev] (slow, CPU)"}
        return {"model": model, "label": labels[model]}

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
