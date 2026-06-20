"""nixx-image: on-demand image generation and editing service.

Generation models (switch via GET/POST /generate-model):
  - sd14        CompVis/stable-diffusion-v1-4              GPU only,         ~2 GiB  [default]
  - sd21        sd2-community/stable-diffusion-2-1         GPU only,       ~3.5 GiB
  - sdxl        stabilityai/stable-diffusion-xl-base-1.0   model offload,  ~6.9 GiB
  - sdxl_turbo  stabilityai/sdxl-turbo                     model offload,  ~6.9 GiB  (4-step)

Editing models (switch via GET/POST /edit_model):
  - ip2p        timbrooks/instruct-pix2pix                 GPU only,       ~1.7 GiB  [default]
  - magic_brush osunlp/InstructPix2Pix-MagicBrush          GPU only,       ~1.7 GiB  (IP2P fine-tune)
  - sdxl_edit   stabilityai/stable-diffusion-xl-base-1.0   seq offload,   ~6.9 GiB  (img2img)
  - kontext     black-forest-labs/FLUX.1-Kontext-dev        CPU only,       ~24 GiB

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

SD14_MODEL_ID = "CompVis/stable-diffusion-v1-4"
SD21_MODEL_ID = "sd2-community/stable-diffusion-2-1"
SDXL_MODEL_ID = "stabilityai/stable-diffusion-xl-base-1.0"
SDXL_TURBO_MODEL_ID = "stabilityai/sdxl-turbo"
IP2P_MODEL_ID = "timbrooks/instruct-pix2pix"
MAGIC_BRUSH_MODEL_ID = "osunlp/InstructPix2Pix-MagicBrush"
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


def _safe_filename(filename: str) -> str:
    """Sanitize model-provided filename."""
    import re

    slug = re.sub(r"[^a-z0-9.\ -]", "", filename.lower())
    slug = re.sub(r"\s+", "-", slug).strip("-.")[:80] or "image"
    return slug


class GenerateRequest(BaseModel):
    prompt: str
    filename: str  # provided by the model - e.g. 'red-cat' → saved as 'red-cat-<job6>.png'
    width: int = 768
    height: int = 768
    steps: int = 30  # SD-family default; SDXL Turbo ignores this and always uses 4


class EditRequest(BaseModel):
    prompt: str
    input_path: str
    filename: str  # provided by the model
    width: int = 768
    height: int = 768
    steps: int = 30  # IP2P/MagicBrush use 50 internally; SDXL edit and Kontext use this


# ── Global state ───────────────────────────────────────────────────────────────

# ── Generation pipeline state ─────────────────────────────────────────────────
_sd14_pipe: Any = None
_sd14_lock = threading.Lock()
_sd21_pipe: Any = None
_sd21_lock = threading.Lock()
_sdxl_pipe: Any = None
_sdxl_lock = threading.Lock()
_sdxl_turbo_pipe: Any = None
_sdxl_turbo_lock = threading.Lock()

# ── Editing pipeline state ────────────────────────────────────────────────────
_ip2p_pipe: Any = None
_ip2p_lock = threading.Lock()
_magic_brush_pipe: Any = None
_magic_brush_lock = threading.Lock()
_sdxl_edit_pipe: Any = None
_sdxl_edit_lock = threading.Lock()
_kontext_pipe: Any = None
_kontext_lock = threading.Lock()

# ── Active model selection ────────────────────────────────────────────────────
_active_generate_model: str = "sd21"  # sd14 | sd21 | sdxl | sdxl_turbo
_generate_model_lock = threading.Lock()
_active_edit_model: str = "ip2p"  # ip2p | magic_brush | sdxl_edit | kontext
_edit_model_lock = threading.Lock()

_jobs: dict[str, dict[str, Any]] = {}  # job_id -> {status, result, error}
_last_request: float = time.monotonic()


def _unload_sd14() -> None:
    global _sd14_pipe
    if _sd14_pipe is None:
        return
    with _sd14_lock:
        _sd14_pipe = None
    import gc

    gc.collect()
    torch.cuda.empty_cache()
    logger.info("SD 1.4 unloaded.")


def _unload_sd21() -> None:
    global _sd21_pipe
    if _sd21_pipe is None:
        return
    with _sd21_lock:
        _sd21_pipe = None
    import gc

    gc.collect()
    torch.cuda.empty_cache()
    logger.info("SD 2.1 unloaded.")


def _unload_sdxl() -> None:
    global _sdxl_pipe
    if _sdxl_pipe is None:
        return
    with _sdxl_lock:
        _sdxl_pipe = None
    import gc

    gc.collect()
    torch.cuda.empty_cache()
    logger.info("SDXL unloaded.")


def _unload_sdxl_turbo() -> None:
    global _sdxl_turbo_pipe
    if _sdxl_turbo_pipe is None:
        return
    with _sdxl_turbo_lock:
        _sdxl_turbo_pipe = None
    import gc

    gc.collect()
    torch.cuda.empty_cache()
    logger.info("SDXL Turbo unloaded.")


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


def _unload_magic_brush() -> None:
    global _magic_brush_pipe
    if _magic_brush_pipe is None:
        return
    with _magic_brush_lock:
        _magic_brush_pipe = None
    import gc

    gc.collect()
    torch.cuda.empty_cache()
    logger.info("MagicBrush unloaded.")


def _unload_sdxl_edit() -> None:
    global _sdxl_edit_pipe
    if _sdxl_edit_pipe is None:
        return
    with _sdxl_edit_lock:
        _sdxl_edit_pipe = None
    import gc

    gc.collect()
    torch.cuda.empty_cache()
    logger.info("SDXL img2img edit unloaded.")


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


def _load_sd14() -> Any:
    global _sd14_pipe
    if _sd14_pipe is not None:
        return _sd14_pipe
    _unload_sd21()
    _unload_sdxl()
    _unload_sdxl_turbo()
    _unload_ip2p()
    _unload_magic_brush()
    _unload_sdxl_edit()
    _unload_kontext()
    with _sd14_lock:
        if _sd14_pipe is not None:
            return _sd14_pipe
        logger.info("Loading SD 1.4...")
        from diffusers import StableDiffusionPipeline

        p = StableDiffusionPipeline.from_pretrained(
            SD14_MODEL_ID,
            torch_dtype=torch.float16,
            safety_checker=None,
        )
        p.to("cuda")
        _sd14_pipe = p
        logger.info("SD 1.4 loaded.")
        return _sd14_pipe


def _load_sd21() -> Any:
    global _sd21_pipe
    if _sd21_pipe is not None:
        return _sd21_pipe
    _unload_sd14()
    _unload_sdxl()
    _unload_sdxl_turbo()
    _unload_ip2p()
    _unload_magic_brush()
    _unload_sdxl_edit()
    _unload_kontext()
    with _sd21_lock:
        if _sd21_pipe is not None:
            return _sd21_pipe
        logger.info("Loading SD 2.1...")
        from diffusers import StableDiffusionPipeline

        p = StableDiffusionPipeline.from_pretrained(
            SD21_MODEL_ID,
            torch_dtype=torch.float16,
            safety_checker=None,
        )
        p.to("cuda")
        _sd21_pipe = p
        logger.info("SD 2.1 loaded.")
        return _sd21_pipe


def _load_sdxl() -> Any:
    global _sdxl_pipe
    if _sdxl_pipe is not None:
        return _sdxl_pipe
    _unload_sd14()
    _unload_sd21()
    _unload_sdxl_turbo()
    _unload_ip2p()
    _unload_magic_brush()
    _unload_sdxl_edit()
    _unload_kontext()
    with _sdxl_lock:
        if _sdxl_pipe is not None:
            return _sdxl_pipe
        logger.info("Loading SDXL...")
        from diffusers.pipelines.stable_diffusion_xl.pipeline_stable_diffusion_xl import (
            StableDiffusionXLPipeline,
        )

        p = StableDiffusionXLPipeline.from_pretrained(
            SDXL_MODEL_ID,
            torch_dtype=torch.float16,
            use_safetensors=True,
        )
        p.enable_model_cpu_offload()
        p.vae.enable_slicing()
        p.vae.enable_tiling()
        _sdxl_pipe = p
        logger.info("SDXL loaded.")
        return _sdxl_pipe


def _load_sdxl_turbo() -> Any:
    global _sdxl_turbo_pipe
    if _sdxl_turbo_pipe is not None:
        return _sdxl_turbo_pipe
    _unload_sd14()
    _unload_sd21()
    _unload_sdxl()
    _unload_ip2p()
    _unload_magic_brush()
    _unload_sdxl_edit()
    _unload_kontext()
    with _sdxl_turbo_lock:
        if _sdxl_turbo_pipe is not None:
            return _sdxl_turbo_pipe
        logger.info("Loading SDXL Turbo...")
        from diffusers.pipelines.stable_diffusion_xl.pipeline_stable_diffusion_xl import (
            StableDiffusionXLPipeline,
        )

        p = StableDiffusionXLPipeline.from_pretrained(
            SDXL_TURBO_MODEL_ID,
            torch_dtype=torch.float16,
            use_safetensors=True,
        )
        p.enable_model_cpu_offload()
        _sdxl_turbo_pipe = p
        logger.info("SDXL Turbo loaded.")
        return _sdxl_turbo_pipe


def _load_ip2p() -> Any:
    global _ip2p_pipe
    if _ip2p_pipe is not None:
        return _ip2p_pipe
    _unload_sd14()
    _unload_sd21()
    _unload_sdxl()
    _unload_sdxl_turbo()
    _unload_magic_brush()
    _unload_sdxl_edit()
    _unload_kontext()
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


def _load_magic_brush() -> Any:
    global _magic_brush_pipe
    if _magic_brush_pipe is not None:
        return _magic_brush_pipe
    _unload_sd14()
    _unload_sd21()
    _unload_sdxl()
    _unload_sdxl_turbo()
    _unload_ip2p()
    _unload_sdxl_edit()
    _unload_kontext()
    with _magic_brush_lock:
        if _magic_brush_pipe is not None:
            return _magic_brush_pipe
        logger.info("Loading MagicBrush...")
        from diffusers import StableDiffusionInstructPix2PixPipeline

        p = StableDiffusionInstructPix2PixPipeline.from_pretrained(
            MAGIC_BRUSH_MODEL_ID,
            torch_dtype=torch.float16,
            safety_checker=None,
        )
        p.to("cuda")
        _magic_brush_pipe = p
        logger.info("MagicBrush loaded.")
        return _magic_brush_pipe


def _load_sdxl_edit() -> Any:
    global _sdxl_edit_pipe
    if _sdxl_edit_pipe is not None:
        return _sdxl_edit_pipe
    _unload_sd14()
    _unload_sd21()
    _unload_sdxl()
    _unload_sdxl_turbo()
    _unload_ip2p()
    _unload_magic_brush()
    _unload_kontext()
    with _sdxl_edit_lock:
        if _sdxl_edit_pipe is not None:
            return _sdxl_edit_pipe
        logger.info("Loading SDXL img2img edit...")
        from diffusers.pipelines.stable_diffusion_xl.pipeline_stable_diffusion_xl_img2img import (
            StableDiffusionXLImg2ImgPipeline,
        )

        p = StableDiffusionXLImg2ImgPipeline.from_pretrained(
            SDXL_MODEL_ID,
            torch_dtype=torch.float16,
            use_safetensors=True,
        )
        p.enable_sequential_cpu_offload()  # peak VRAM ~3-4 GiB on GTX 1070
        p.vae.enable_slicing()
        p.vae.enable_tiling()
        _sdxl_edit_pipe = p
        logger.info("SDXL img2img edit loaded.")
        return _sdxl_edit_pipe


def _load_kontext() -> Any:
    global _kontext_pipe
    if _kontext_pipe is not None:
        return _kontext_pipe
    _unload_sd14()
    _unload_sd21()
    _unload_sdxl()
    _unload_sdxl_turbo()
    _unload_ip2p()
    _unload_magic_brush()
    _unload_sdxl_edit()
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


def _run_generate(
    job_id: str, prompt: str, filename: str, width: int, height: int, steps: int
) -> None:
    """Dispatch to the active generation model."""
    if _active_generate_model in ("sdxl", "sdxl_turbo"):
        _run_generate_sdxl(job_id, prompt, filename, width, height, steps)
    else:
        _run_generate_sd(job_id, prompt, filename, width, height, steps)


def _run_generate_sd(
    job_id: str, prompt: str, filename: str, width: int, height: int, steps: int
) -> None:
    global _last_request
    try:
        pipe = _load_sd14() if _active_generate_model == "sd14" else _load_sd21()
        _last_request = time.monotonic()
        logger.info("Generating image (%s) for job %s", _active_generate_model, job_id)
        w = (width // 8) * 8 or 512
        h = (height // 8) * 8 or 512
        result = pipe(prompt=prompt, width=w, height=h, num_inference_steps=steps or 30)
        img = result.images[0]
        out_path = OUTPUT_DIR / f"{_safe_filename(filename)}.png"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path)
        _jobs[job_id]["status"] = "done"
        _jobs[job_id]["result"] = str(out_path)
        _jobs[job_id]["completed_at"] = time.time()
        _jobs[job_id]["latency_ms"] = int(
            (_jobs[job_id]["completed_at"] - _jobs[job_id]["submitted_at"]) * 1000
        )
        logger.info("Job %s done: %s", job_id, out_path)
        _append_job_log(job_id, _jobs[job_id])
    except Exception as exc:
        logger.exception("Job %s failed", job_id)
        _jobs[job_id]["status"] = "error"
        _jobs[job_id]["error"] = str(exc)
        _jobs[job_id]["completed_at"] = time.time()
        _append_job_log(job_id, _jobs[job_id])
    finally:
        import torch

        torch.cuda.empty_cache()
        _last_request = time.monotonic()


def _run_edit_ip2p(
    job_id: str,
    prompt: str,
    input_path: str,
    filename: str,
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
            guidance_scale=7.5,  # how strongly to follow text prompt
        )
        img = result.images[0]
        out_path = OUTPUT_DIR / f"{_safe_filename(filename)}.png"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path)
        _jobs[job_id]["status"] = "done"
        _jobs[job_id]["result"] = str(out_path)
        _jobs[job_id]["completed_at"] = time.time()
        _jobs[job_id]["latency_ms"] = int(
            (_jobs[job_id]["completed_at"] - _jobs[job_id]["submitted_at"]) * 1000
        )
        logger.info("Job %s done: %s", job_id, out_path)
        _append_job_log(job_id, _jobs[job_id])
    except Exception as exc:
        logger.exception("Job %s failed", job_id)
        _jobs[job_id]["status"] = "error"
        _jobs[job_id]["error"] = str(exc)
        _jobs[job_id]["completed_at"] = time.time()
        _append_job_log(job_id, _jobs[job_id])
    finally:
        import torch

        torch.cuda.empty_cache()
        _last_request = time.monotonic()


def _run_edit_kontext(
    job_id: str,
    prompt: str,
    input_path: str,
    filename: str,
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
        out_path = OUTPUT_DIR / f"{_safe_filename(filename)}.png"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path)
        _jobs[job_id]["status"] = "done"
        _jobs[job_id]["result"] = str(out_path)
        _jobs[job_id]["completed_at"] = time.time()
        _jobs[job_id]["latency_ms"] = int(
            (_jobs[job_id]["completed_at"] - _jobs[job_id]["submitted_at"]) * 1000
        )
        logger.info("Job %s done: %s", job_id, out_path)
        _append_job_log(job_id, _jobs[job_id])
    except Exception as exc:
        logger.exception("Job %s failed", job_id)
        _jobs[job_id]["status"] = "error"
        _jobs[job_id]["error"] = str(exc)
        _jobs[job_id]["completed_at"] = time.time()
        _append_job_log(job_id, _jobs[job_id])
    finally:
        import torch

        torch.cuda.empty_cache()
        # Restore GPU visibility so GPU generate/edit models can use CUDA later
        os.environ["CUDA_VISIBLE_DEVICES"] = "1"
        _last_request = time.monotonic()


def _run_edit_magic_brush(
    job_id: str,
    prompt: str,
    input_path: str,
    filename: str,
    width: int,
    height: int,
) -> None:
    global _last_request
    try:
        from PIL import Image, ImageOps

        pipe = _load_magic_brush()
        _last_request = time.monotonic()
        logger.info("Editing image (MagicBrush) for job %s", job_id)
        w = (width // 8) * 8 or 512
        h = (height // 8) * 8 or 512
        input_image = ImageOps.exif_transpose(Image.open(input_path).convert("RGB")).resize((w, h))
        result = pipe(
            prompt=prompt,
            image=input_image,
            num_inference_steps=50,
            image_guidance_scale=1.5,
            guidance_scale=7.5,
        )
        img = result.images[0]
        out_path = OUTPUT_DIR / f"{_safe_filename(filename)}.png"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path)
        _jobs[job_id]["status"] = "done"
        _jobs[job_id]["result"] = str(out_path)
        _jobs[job_id]["completed_at"] = time.time()
        _jobs[job_id]["latency_ms"] = int(
            (_jobs[job_id]["completed_at"] - _jobs[job_id]["submitted_at"]) * 1000
        )
        logger.info("Job %s done: %s", job_id, out_path)
        _append_job_log(job_id, _jobs[job_id])
    except Exception as exc:
        logger.exception("Job %s failed", job_id)
        _jobs[job_id]["status"] = "error"
        _jobs[job_id]["error"] = str(exc)
        _jobs[job_id]["completed_at"] = time.time()
        _append_job_log(job_id, _jobs[job_id])
    finally:
        import torch

        torch.cuda.empty_cache()
        _last_request = time.monotonic()


def _run_edit_sdxl(
    job_id: str,
    prompt: str,
    input_path: str,
    filename: str,
    width: int,
    height: int,
    steps: int,
) -> None:
    """SDXL img2img editing. Describe the desired output image (not the change to make)."""
    global _last_request, _active_edit_model
    try:
        from PIL import Image, ImageOps

        pipe = _load_sdxl_edit()
        _last_request = time.monotonic()
        logger.info("Editing image (SDXL img2img) for job %s", job_id)
        w = (width // 64) * 64 or 768
        h = (height // 64) * 64 or 768
        input_image = ImageOps.exif_transpose(Image.open(input_path).convert("RGB")).resize((w, h))
        result = pipe(
            prompt=prompt,
            image=input_image,
            num_inference_steps=steps or 25,
            strength=0.65,
        )
        img = result.images[0]
        out_path = OUTPUT_DIR / f"{_safe_filename(filename)}.png"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path)
        _jobs[job_id]["status"] = "done"
        _jobs[job_id]["result"] = str(out_path)
        _jobs[job_id]["completed_at"] = time.time()
        _jobs[job_id]["latency_ms"] = int(
            (_jobs[job_id]["completed_at"] - _jobs[job_id]["submitted_at"]) * 1000
        )
        logger.info("Job %s done: %s", job_id, out_path)
        _append_job_log(job_id, _jobs[job_id])
    except Exception as exc:
        logger.exception("Job %s failed", job_id)
        # SDXL edit needs more VRAM than the GTX 1070 has. Revert to ip2p
        # so subsequent edit jobs don't keep failing with the stuck model.
        with _edit_model_lock:
            if _active_edit_model == "sdxl_edit":
                _active_edit_model = "ip2p"
                logger.warning("SDXL edit job failed; reverted active edit model to ip2p")
        _jobs[job_id]["status"] = "error"
        _jobs[job_id]["error"] = str(exc)
        _jobs[job_id]["completed_at"] = time.time()
        _append_job_log(job_id, _jobs[job_id])
    finally:
        import torch

        torch.cuda.empty_cache()
        _last_request = time.monotonic()


def _run_generate_sdxl(
    job_id: str, prompt: str, filename: str, width: int, height: int, steps: int
) -> None:
    global _last_request, _active_generate_model
    try:
        if _active_generate_model == "sdxl_turbo":
            pipe = _load_sdxl_turbo()
            _steps = 4
            _guidance = 0.0
        else:
            pipe = _load_sdxl()
            _steps = steps or 25
            _guidance = 5.0
        _last_request = time.monotonic()
        logger.info("Generating image (%s) for job %s", _active_generate_model, job_id)
        w = (width // 64) * 64 or 768
        h = (height // 64) * 64 or 768
        result = pipe(
            prompt=prompt, width=w, height=h, num_inference_steps=_steps, guidance_scale=_guidance
        )
        img = result.images[0]
        out_path = OUTPUT_DIR / f"{_safe_filename(filename)}.png"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path)
        _jobs[job_id]["status"] = "done"
        _jobs[job_id]["result"] = str(out_path)
        _jobs[job_id]["completed_at"] = time.time()
        _jobs[job_id]["latency_ms"] = int(
            (_jobs[job_id]["completed_at"] - _jobs[job_id]["submitted_at"]) * 1000
        )
        logger.info("Job %s done: %s", job_id, out_path)
        _append_job_log(job_id, _jobs[job_id])
    except Exception as exc:
        logger.exception("Job %s failed", job_id)
        # SDXL/SDXL-Turbo need more VRAM than the GTX 1070 has. Revert to sd21
        # so subsequent jobs don't keep failing with the stuck model selection.
        with _generate_model_lock:
            if _active_generate_model in ("sdxl", "sdxl_turbo"):
                _active_generate_model = "sd21"
                logger.warning("SDXL job failed; reverted active generate model to sd21")
        _jobs[job_id]["status"] = "error"
        _jobs[job_id]["error"] = str(exc)
        _jobs[job_id]["completed_at"] = time.time()
        _append_job_log(job_id, _jobs[job_id])
    finally:
        import torch

        torch.cuda.empty_cache()
        _last_request = time.monotonic()


def _run_edit(
    job_id: str,
    prompt: str,
    input_path: str,
    filename: str,
    width: int,
    height: int,
    steps: int,
) -> None:
    """Dispatch to the active edit model."""
    if _active_edit_model == "kontext":
        _run_edit_kontext(job_id, prompt, input_path, filename, width, height, steps)
    elif _active_edit_model == "magic_brush":
        _run_edit_magic_brush(job_id, prompt, input_path, filename, width, height)
    elif _active_edit_model == "sdxl_edit":
        _run_edit_sdxl(job_id, prompt, input_path, filename, width, height, steps)
    else:
        _run_edit_ip2p(job_id, prompt, input_path, filename, width, height)


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
        _EDIT_IDS = {
            "ip2p": IP2P_MODEL_ID,
            "magic_brush": MAGIC_BRUSH_MODEL_ID,
            "sdxl_edit": SDXL_MODEL_ID,
            "kontext": KONTEXT_MODEL_ID,
        }
        _GEN_IDS = {
            "sd14": SD14_MODEL_ID,
            "sd21": SD21_MODEL_ID,
            "sdxl": SDXL_MODEL_ID,
            "sdxl_turbo": SDXL_TURBO_MODEL_ID,
        }
        return {
            "status": "ok",
            "active_generate_model": _active_generate_model,
            "generate_model_id": _GEN_IDS.get(_active_generate_model, ""),
            "active_edit_model": _active_edit_model,
            "edit_model_id": _EDIT_IDS.get(_active_edit_model, ""),
            "sd14_loaded": _sd14_pipe is not None,
            "sd21_loaded": _sd21_pipe is not None,
            "sdxl_loaded": _sdxl_pipe is not None,
            "sdxl_turbo_loaded": _sdxl_turbo_pipe is not None,
            "ip2p_loaded": _ip2p_pipe is not None,
            "magic_brush_loaded": _magic_brush_pipe is not None,
            "sdxl_edit_loaded": _sdxl_edit_pipe is not None,
            "kontext_loaded": _kontext_pipe is not None,
        }

    @app.get("/generate-model")
    async def get_generate_model() -> dict[str, str]:
        return {"model": _active_generate_model}

    @app.post("/generate-model")
    async def set_generate_model(body: dict[str, str]) -> dict[str, str]:
        global _active_generate_model
        model = body.get("model", "").lower()
        _VALID = {"sd14", "sd21", "sdxl", "sdxl_turbo"}
        if model not in _VALID:
            raise HTTPException(status_code=400, detail=f"model must be one of {sorted(_VALID)}")
        with _generate_model_lock:
            _active_generate_model = model
        logger.info("Active generate model set to: %s", model)
        _LABELS = {
            "sd14": "SD 1.4 (GPU only, ~2 GiB)",
            "sd21": "SD 2.1 (GPU only, ~3.5 GiB)",
            "sdxl": "SDXL (model offload, ~6.9 GiB)",
            "sdxl_turbo": "SDXL Turbo (model offload, ~6.9 GiB, 4-step)",
        }
        return {"model": model, "label": _LABELS[model]}

    @app.get("/edit_model")
    async def get_edit_model() -> dict[str, str]:
        return {"model": _active_edit_model}

    @app.post("/edit_model")
    async def set_edit_model(body: dict[str, str]) -> dict[str, str]:
        global _active_edit_model
        model = body.get("model", "").lower()
        _VALID = {"ip2p", "magic_brush", "sdxl_edit", "kontext"}
        if model not in _VALID:
            raise HTTPException(status_code=400, detail=f"model must be one of {sorted(_VALID)}")
        with _edit_model_lock:
            _active_edit_model = model
        logger.info("Active edit model set to: %s", model)
        _LABELS = {
            "ip2p": "InstructPix2Pix (GPU only, ~1.7 GiB)",
            "magic_brush": "MagicBrush (GPU only, ~1.7 GiB)",
            "sdxl_edit": "SDXL img2img (sequential offload, ~6.9 GiB)",
            "kontext": "FLUX.1 Kontext [dev] (CPU only, ~24 GiB)",
        }
        return {"model": model, "label": _LABELS[model]}

    @app.post("/generate")
    async def generate(req: GenerateRequest) -> dict[str, str]:
        global _last_request
        _last_request = time.monotonic()
        job_id = uuid.uuid4().hex
        out_path = OUTPUT_DIR / f"{_safe_filename(req.filename)}.png"
        _jobs[job_id] = {
            "status": "running",
            "type": "generate",
            "result": None,
            "error": None,
            "submitted_at": time.time(),
            "completed_at": None,
            "latency_ms": None,
        }
        t = threading.Thread(
            target=_run_generate,
            args=(job_id, req.prompt, req.filename, req.width, req.height, req.steps),
            daemon=True,
        )
        t.start()
        return {"job_id": job_id, "status": "running", "path": str(out_path)}

    @app.post("/edit")
    async def edit(req: EditRequest) -> dict[str, str]:
        global _last_request
        _last_request = time.monotonic()
        if not Path(req.input_path).exists():
            raise HTTPException(status_code=400, detail=f"Input file not found: {req.input_path}")
        job_id = uuid.uuid4().hex
        out_path = OUTPUT_DIR / f"{_safe_filename(req.filename)}.png"
        _jobs[job_id] = {
            "status": "running",
            "type": "edit",
            "result": None,
            "error": None,
            "submitted_at": time.time(),
            "completed_at": None,
            "latency_ms": None,
        }
        t = threading.Thread(
            target=_run_edit,
            args=(
                job_id,
                req.prompt,
                req.input_path,
                req.filename,
                req.width,
                req.height,
                req.steps,
            ),
            daemon=True,
        )
        t.start()
        return {"job_id": job_id, "status": "running", "path": str(out_path)}

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
        return {
            "jobs": jobs,
            "total": len(jobs),
            "done": sum(1 for j in _jobs.values() if j["status"] == "done"),
        }

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
