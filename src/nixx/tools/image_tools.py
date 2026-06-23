"""ImageTool: wake nixx-image service and submit generation/editing jobs."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx

from nixx.tools.base import Tool, ToolResult

logger = logging.getLogger(__name__)

IMAGE_SERVICE_URL = "http://127.0.0.1:8090"
_STARTUP_TIMEOUT = 60  # seconds to wait for service to come up


async def _ensure_running() -> bool:
    """Start nixx-image if not running; wait up to _STARTUP_TIMEOUT seconds."""
    # Check if already up
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{IMAGE_SERVICE_URL}/health")
            if r.status_code == 200:
                return True
    except Exception:
        pass

    # Not running - start it
    logger.info("nixx-image not running, starting via systemctl...")
    proc = await asyncio.create_subprocess_exec(
        "sudo",
        "systemctl",
        "start",
        "nixx-image",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.error(
            "systemctl start nixx-image failed (rc=%d): %s",
            proc.returncode,
            stderr.decode().strip(),
        )
        return False

    # Poll until up or timeout
    deadline = asyncio.get_event_loop().time() + _STARTUP_TIMEOUT
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(3)
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"{IMAGE_SERVICE_URL}/health")
                if r.status_code == 200:
                    logger.info("nixx-image is up.")
                    return True
        except Exception:
            pass
    logger.error("nixx-image did not respond within %ds", _STARTUP_TIMEOUT)
    return False


class GenerateImageTool(Tool):
    """Generate an image from a text prompt using FLUX.1 Kontext."""

    def __init__(self, scratch_dir: Path) -> None:
        self._scratch_dir = scratch_dir

    @property
    def name(self) -> str:
        return "generate_image"

    @property
    def description(self) -> str:
        return (
            "Generate an image from a text prompt using FLUX.1 Schnell (fast, ~1 min on this hardware). "
            "Non-blocking: starts the job and returns immediately. "
            "The image is saved to ~/nixx_scratch/images/<filename>.png — the filename parameter YOU provide determines the saved file name. "
            "Choose a short, descriptive filename from the prompt (e.g. 'space-kid', 'red-barn-sunset'). "
            "Default is 4 steps (good quality for Schnell). Increase steps only if quality is poor."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Detailed text prompt describing the image to generate.",
                },
                "filename": {
                    "type": "string",
                    "description": (
                        "Output filename (without extension). 1-3 words, lowercase, hyphen-separated, "
                        "no special characters. Describe the main subject. "
                        "For revisions of a similar image use dotted versions: dog, dog.1, dog.2"
                    ),
                },
                "width": {"type": "integer", "default": 768, "description": "Image width in px."},
                "height": {
                    "type": "integer",
                    "default": 768,
                    "description": "Image height in px.",
                },
                "steps": {
                    "type": "integer",
                    "default": 4,
                    "description": "Number of inference steps (more = higher quality, slower).",
                },
            },
            "required": ["prompt", "filename"],
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        prompt = kwargs.get("prompt", "")
        filename = kwargs.get("filename", "image")
        if not prompt:
            return ToolResult(success=False, error="prompt is required")

        if not await _ensure_running():
            return ToolResult(
                success=False,
                error="nixx-image service failed to start within timeout.",
            )

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(
                    f"{IMAGE_SERVICE_URL}/generate",
                    json={
                        "prompt": prompt,
                        "filename": filename,
                        "width": int(kwargs.get("width") or 768),
                        "height": int(kwargs.get("height") or 768),
                        "steps": int(kwargs.get("steps") or 4),
                    },
                )
                if not r.is_success:
                    return ToolResult(success=False, error=f"HTTP {r.status_code}: {r.text[:200]}")
                data = r.json()
                job_id = data["job_id"]
                out = data["path"]
                return ToolResult(
                    success=True,
                    result=(
                        f"# job_id={job_id} (internal tracking — do not repeat to user)\n"
                        "Generation queued. SD ~1-3 min, SDXL ~3-5 min.\n"
                        "Call image_status(job_id) to check — the PWA displays the image automatically when done."
                    ),
                    metadata={"job_id": job_id, "path": out},
                )
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


class EditImageTool(Tool):
    """Edit an existing image using a text prompt with FLUX.1 Kontext."""

    def __init__(self, scratch_dir: Path) -> None:
        self._scratch_dir = scratch_dir

    @property
    def name(self) -> str:
        return "edit_image"

    @property
    def description(self) -> str:
        return (
            "Edit an existing image using a text prompt. "
            "PROMPT STYLE: Use imperative instructions that describe the change, not the result. "
            "Good: 'make the sky orange', 'add a hat to the person', 'turn it to winter'. "
            "Bad: 'an orange sky', 'a person wearing a hat'. "
            "Two edit models are available (switch with /image-model in chat): "
            "'ip2p' uses InstructPix2Pix (GPU, ~1-2 min, default) and 'kontext' uses FLUX.1 Kontext [dev] (CPU only, ~30 hours — ~72 min/step × 28 steps on pyrite's CPU). "
            "Use this when you need to modify an existing image - for new images use generate_image instead. "
            "The input image must be an absolute path to a PNG/JPG in the scratch directory. "
            "Non-blocking: starts the job and returns immediately. "
            "The edited image is saved to ~/nixx_scratch/images/<filename>.png when done. "
            "Guidance: image_guidance controls how much of the original is preserved (lower = stronger edit, default 1.0). "
            "text_guidance controls how strongly the instruction is followed (higher = stronger edit, default 9.5). "
            "For subtle tweaks use image_guidance=1.5, text_guidance=7.5. For dramatic changes use image_guidance=0.8, text_guidance=12.0."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Text prompt describing the desired edit.",
                },
                "input_path": {
                    "type": "string",
                    "description": "Absolute path to the input image (must be in scratch directory).",
                },
                "filename": {
                    "type": "string",
                    "description": (
                        "Output filename (without extension). 1-3 words, lowercase, hyphen-separated, "
                        "no special characters. Describe the main subject. "
                        "For revisions of a similar image use dotted versions: dog, dog.1, dog.2"
                    ),
                },
                "width": {
                    "type": "integer",
                    "default": 768,
                    "description": "Output width in px. Default 768 - do not set unless user asks to resize. Max 768.",
                },
                "height": {
                    "type": "integer",
                    "default": 768,
                    "description": "Output height in px. Default 768 - do not set unless user asks to resize. Max 768.",
                },
                "steps": {"type": "integer", "default": 28},
                "image_guidance": {
                    "type": "number",
                    "default": 1.0,
                    "description": "IP2P/MagicBrush: how much to preserve the source image. Range 0.5-2.5. Lower = stronger edit. Default 1.0.",
                },
                "text_guidance": {
                    "type": "number",
                    "default": 9.5,
                    "description": "IP2P/MagicBrush: how strongly to follow the instruction. Range 5.0-15.0. Higher = stronger edit. Default 9.5.",
                },
            },
            "required": ["prompt", "input_path", "filename"],
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        prompt = kwargs.get("prompt", "")
        input_path = kwargs.get("input_path", "")
        filename = kwargs.get("filename", "edit")
        if not prompt or not input_path:
            return ToolResult(success=False, error="prompt and input_path are required")

        if not await _ensure_running():
            return ToolResult(
                success=False,
                error="nixx-image service failed to start within timeout.",
            )

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(
                    f"{IMAGE_SERVICE_URL}/edit",
                    json={
                        "prompt": prompt,
                        "input_path": input_path,
                        "filename": filename,
                        "width": int(kwargs.get("width") or 768),
                        "height": int(kwargs.get("height") or 768),
                        "steps": int(kwargs.get("steps") or 28),
                        "image_guidance": float(kwargs.get("image_guidance") or 1.0),
                        "text_guidance": float(kwargs.get("text_guidance") or 9.5),
                    },
                )
                if not r.is_success:
                    return ToolResult(success=False, error=f"HTTP {r.status_code}: {r.text[:200]}")
                data = r.json()
                job_id = data["job_id"]
                out = data["path"]
                return ToolResult(
                    success=True,
                    result=(
                        f"# job_id={job_id} (internal tracking — do not repeat to user)\n"
                        "Edit queued. IP2P/MagicBrush ~1-2 min, SDXL edit ~3-5 min, Kontext ~30 hours (CPU, 28 steps × ~72 min each).\n"
                        "Call image_status(job_id) to check — the PWA displays the image automatically when done."
                    ),
                    metadata={"job_id": job_id, "path": out},
                )
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


class ImageStatusTool(Tool):
    """Check the status of image generation or editing jobs."""

    @property
    def name(self) -> str:
        return "image_status"

    @property
    def description(self) -> str:
        return (
            "Check the status of image generation or editing jobs on nixx-image. "
            "Call this proactively after starting a job to report progress to the user - "
            "do not tell the user to check themselves. "
            "If job_id is given, returns detail for that specific job (status, elapsed time, error). "
            "If omitted, lists all recent jobs with their current status. "
            "Do NOT repeat job IDs or file paths to the user — just say whether the image is ready or still generating."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "Optional job ID to check. Omit to list all jobs.",
                },
            },
            "required": [],
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        import time

        job_id = kwargs.get("job_id", "").strip()

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                if job_id:
                    r = await client.get(f"{IMAGE_SERVICE_URL}/status/{job_id}")
                    if r.status_code == 404:
                        return ToolResult(success=False, error=f"Job {job_id!r} not found.")
                    r.raise_for_status()
                    j = r.json()
                    status = j.get("status", "unknown")
                    submitted = j.get("submitted_at")
                    latency_ms = j.get("latency_ms")
                    error = j.get("error")

                    if latency_ms is not None:
                        elapsed = f"{latency_ms / 60000:.1f}m"
                    elif submitted:
                        elapsed = f"{(time.time() - submitted) / 60:.1f}m (still running)"
                    else:
                        elapsed = "unknown"

                    lines = [f"Job {job_id}: {status}, elapsed {elapsed}"]
                    if error:
                        lines.append(f"Error: {error}")
                    return ToolResult(success=True, result="\n".join(lines))

                else:
                    # Service may be down (idle shutdown) - return gracefully
                    try:
                        r = await client.get(f"{IMAGE_SERVICE_URL}/jobs")
                        r.raise_for_status()
                        data = r.json()
                        jobs = data.get("jobs", [])
                    except Exception:
                        return ToolResult(
                            success=True,
                            result="nixx-image is not running (idle shutdown). No active jobs.",
                        )

                    if not jobs:
                        return ToolResult(success=True, result="No image jobs found.")

                    lines = []
                    for j in jobs:
                        jid = j["job_id"]
                        status = j["status"]
                        jtype = j.get("type", "?")
                        latency_ms = j.get("latency_ms")
                        submitted = j.get("submitted_at")
                        if latency_ms is not None:
                            elapsed = f"{latency_ms / 60000:.1f}m"
                        elif submitted:
                            elapsed = f"{(time.time() - submitted) / 60:.1f}m (running)"
                        else:
                            elapsed = "?"
                        lines.append(f"{jid[:8]}  {jtype:<8}  {status:<8}  {elapsed}")
                    return ToolResult(success=True, result="\n".join(lines))

        except Exception as exc:
            return ToolResult(success=False, error=str(exc))
