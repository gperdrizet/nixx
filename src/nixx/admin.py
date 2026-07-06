"""Standalone admin dashboard for nixx.

Runs on port 8001, binds to Tailscale IP only. No nixx imports - reads DB
and calls systemctl directly. Start with: nixx-admin (entry point) or
uvicorn nixx.admin:create_app --factory --host 100.64.0.2 --port 8001
"""

from __future__ import annotations

import json
from typing import cast
import subprocess
from pathlib import Path
from typing import Any

import asyncpg
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse


def create_app() -> FastAPI:
    app = FastAPI(title="nixx admin", docs_url=None, redoc_url=None)

    # ── DB connection (lazy, per-request) ─────────────────────────────────────

    async def _db() -> asyncpg.Connection:
        from nixx.config import NixxConfig  # only for DATABASE_URL

        cfg = NixxConfig()
        return cast(asyncpg.Connection, await asyncpg.connect(cfg.database_url))

    # ── Service status ────────────────────────────────────────────────────────

    def _service_status(name: str, unit: str | None = None) -> dict[str, str]:
        unit = unit or name
        result: dict[str, str] = {"name": name, "state": "unknown", "uptime": ""}
        try:
            r = subprocess.run(
                ["systemctl", "is-active", unit],
                capture_output=True,
                text=True,
                timeout=5,
            )
            result["state"] = r.stdout.strip()
        except Exception:
            pass
        try:
            r2 = subprocess.run(
                ["systemctl", "show", unit, "--property=ActiveEnterTimestamp"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            ts_str = r2.stdout.strip().split("=", 1)[-1].strip()
            if ts_str:
                from datetime import datetime

                fmt = "%a %Y-%m-%d %H:%M:%S %Z"
                try:
                    started = datetime.strptime(ts_str, fmt)
                    # systemctl timestamps are in local time; get elapsed in seconds

                    local_now = datetime.now()
                    elapsed = int(local_now.timestamp() - started.timestamp())
                    if elapsed < 0:
                        elapsed = 0
                    if elapsed < 3600:
                        result["uptime"] = f"{elapsed // 60}m"
                    elif elapsed < 86400:
                        result["uptime"] = f"{elapsed // 3600}h {(elapsed % 3600) // 60}m"
                    else:
                        result["uptime"] = f"{elapsed // 86400}d {(elapsed % 86400) // 3600}h"
                except ValueError:
                    pass
        except Exception:
            pass
        return result

    # ── Routes ────────────────────────────────────────────────────────────────

    @app.get("/admin/api/status")
    async def get_status() -> dict[str, Any]:
        from nixx.config import NixxConfig

        cfg = NixxConfig()

        # Fetch live model selection from nixx-image health endpoint
        _image_svc = _service_status("nixx-image")
        _image_model_label = "not running"
        if _image_svc.get("active"):
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    _hr = await client.get("http://127.0.0.1:8090/health")
                    if _hr.status_code == 200:
                        _hd = _hr.json()
                        _image_model_label = _hd.get("active_generate_model", "?")
            except Exception:
                _image_model_label = "unknown"

        services = [
            _service_status("nixx-server"),
            _service_status("postgresql", "docker"),
            {**_service_status("nixx-embed"), "model": cfg.embedding_model},
            {**_service_status("llamacpp", "llamacpp.service"), "model": cfg.llm_model},
            {**_image_svc, "on_demand": True, "model": _image_model_label},
        ]
        # SearXNG: HTTP probe (Docker container, no systemd unit)
        searxng_state = "unknown"
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(cfg.searxng_url + "/healthz")
                searxng_state = "active" if r.status_code == 200 else "inactive"
        except Exception:
            searxng_state = "inactive"
        services.append({"name": "searxng", "state": searxng_state, "uptime": ""})
        # Count generated images from scratch dir
        images_dir = cfg.scratch_dir / "images"
        images_generated = len(list(images_dir.glob("*.png"))) if images_dir.exists() else 0
        conn = await _db()
        try:
            rows = await conn.fetch("""
                SELECT
                    (SELECT COUNT(*) FROM buffer)         AS buffer_rows,
                    (SELECT COUNT(*) FROM buffer WHERE role = 'user')       AS user_msgs,
                    (SELECT COUNT(*) FROM buffer WHERE role = 'assistant')  AS asst_msgs,
                    (SELECT COUNT(*) FROM summaries)      AS summaries,
                    (SELECT COUNT(*) FROM sources)        AS sources,
                    (SELECT COUNT(*) FROM memories)       AS memory_chunks,
                    (SELECT COALESCE(SUM(tool_calls_made), 0) FROM buffer WHERE role = 'assistant') AS total_tool_calls,
                    (SELECT COALESCE(SUM(prompt_tokens), 0) + COALESCE(SUM(completion_tokens), 0) FROM buffer WHERE role = 'assistant') AS total_tokens,
                    (SELECT COALESCE(SUM(prompt_tokens), 0) FROM buffer WHERE role = 'assistant') AS total_prompt_tokens,
                    (SELECT COALESCE(SUM(completion_tokens), 0) FROM buffer WHERE role = 'assistant') AS total_completion_tokens
                """)
            db = dict(rows[0])
            db["images_generated"] = images_generated
        except Exception as exc:
            db = {"error": str(exc)}
        finally:
            await conn.close()
        return {"services": services, "db": db}

    @app.get("/admin/api/message-history")
    async def get_message_history(limit: int = 50) -> dict[str, Any]:
        conn = await _db()
        try:
            rows = await conn.fetch(
                """
                SELECT id, role, created_at,
                       LENGTH(content) AS char_count
                FROM buffer
                WHERE role IN ('user', 'assistant')
                ORDER BY id DESC
                LIMIT $1
                """,
                limit,
            )
            return {
                "history": [
                    {
                        "id": r["id"],
                        "role": r["role"],
                        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                        "char_count": r["char_count"],
                    }
                    for r in reversed(rows)
                ]
            }
        finally:
            await conn.close()

    @app.get("/admin/api/recent-buffer")
    async def get_recent_buffer(limit: int = 20) -> dict[str, Any]:
        conn = await _db()
        try:
            rows = await conn.fetch(
                """
                SELECT id, role, content, created_at
                FROM buffer ORDER BY id DESC LIMIT $1
                """,
                limit,
            )
            return {
                "entries": [
                    {
                        "id": r["id"],
                        "role": r["role"],
                        "snippet": (r["content"] or "")[:120],
                        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                    }
                    for r in reversed(rows)
                ]
            }
        finally:
            await conn.close()

    @app.get("/admin/api/metrics")
    async def get_metrics(limit: int = 60) -> dict[str, Any]:
        """Return per-response metrics for the last N assistant messages."""
        conn = await _db()
        # Read persistent image job log (survives image service restarts)
        _image_job_log = Path.home() / "nixx_scratch" / "image_jobs.jsonl"
        image_jobs: list[dict[str, Any]] = []
        if _image_job_log.exists():
            try:
                with _image_job_log.open() as _f:
                    for _line in _f:
                        _line = _line.strip()
                        if _line:
                            image_jobs.append(json.loads(_line))
            except Exception:
                pass
        # Merge with live in-memory jobs (may include jobs not yet in the log)
        live_job_ids = {j["job_id"] for j in image_jobs}
        try:
            async with httpx.AsyncClient(timeout=1.0) as client:
                r = await client.get("http://127.0.0.1:8090/jobs")
                if r.status_code == 200:
                    for j in r.json().get("jobs", []):
                        if j.get("job_id") not in live_job_ids:
                            image_jobs.append(j)
        except Exception:
            pass
        # Fetch per-tool usage from nixx-server (best-effort)
        tool_usage: dict[str, int] = {}
        try:
            async with httpx.AsyncClient(timeout=1.0) as client:
                r = await client.get("http://127.0.0.1:8000/v1/debug/tool-usage")
                if r.status_code == 200:
                    tool_usage = r.json().get("tool_usage", {})
        except Exception:
            pass
        try:
            rows = await conn.fetch(
                """
                SELECT id, created_at,
                       prompt_tokens, completion_tokens,
                       latency_ms, tool_calls_made,
                       LENGTH(content) AS char_count
                FROM buffer
                WHERE role = 'assistant'
                ORDER BY id DESC
                LIMIT $1
                """,
                limit,
            )
            # Aggregate tool call stats
            tool_rows = await conn.fetch("""
                SELECT COALESCE(tool_calls_made, 0) AS n
                FROM buffer
                WHERE role = 'assistant'
                  AND tool_calls_made IS NOT NULL
                """)
            total_tool_calls = sum(r["n"] for r in tool_rows)
            no_tools = await conn.fetchval(
                "SELECT COUNT(*) FROM buffer WHERE role = 'assistant' AND tool_calls_made IS NULL"
            )
            return {
                "history": [
                    {
                        "id": r["id"],
                        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                        "prompt_tokens": r["prompt_tokens"],
                        "completion_tokens": r["completion_tokens"],
                        "latency_ms": r["latency_ms"],
                        "tool_calls_made": r["tool_calls_made"],
                        "char_count": r["char_count"],
                    }
                    for r in reversed(rows)
                ],
                "tool_summary": {
                    "total_tool_calls": total_tool_calls,
                    "responses_with_tools": len(tool_rows),
                    "responses_without_tools": int(no_tools),
                },
                "image_jobs": [
                    {
                        "job_id": j.get("job_id"),
                        "status": j.get("status"),
                        "type": j.get("type"),
                        "latency_ms": j.get("latency_ms"),
                        "submitted_at": j.get("submitted_at"),
                    }
                    for j in image_jobs
                    if j.get("status") == "done" and j.get("latency_ms") is not None
                ],
                "tool_usage": tool_usage,
            }
        finally:
            await conn.close()

    @app.post("/admin/api/restart/{service}")
    async def restart_service(service: str) -> dict[str, str]:
        allowed = {"nixx-server", "nixx-embed", "nixx-image"}
        if service not in allowed:
            raise HTTPException(status_code=400, detail=f"Unknown service: {service}")
        try:
            r = subprocess.run(
                ["sudo", "systemctl", "restart", service],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if r.returncode != 0:
                raise HTTPException(status_code=500, detail=r.stderr.strip())
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="Restart timed out")
        return {"restarted": service}

    @app.get("/admin/api/config")
    async def get_config() -> dict[str, Any]:
        from nixx.config import NixxConfig

        cfg = NixxConfig()
        conn = await _db()
        try:
            intent_row = await conn.fetchrow("SELECT value FROM state WHERE key = 'intent'")
            project_dir_row = await conn.fetchrow(
                "SELECT value FROM state WHERE key = 'project_dir'"
            )
        finally:
            await conn.close()
        intent = intent_row["value"] if intent_row else None
        project_dir = project_dir_row["value"] if project_dir_row else None
        tools = [
            "read_file",
            "write_file",
            "edit_file",
            "list_dir",
            "delete_file",
            "read_plan",
            "write_plan",
            "run_python",
            "web_search",
            "read_webpage",
            "validate_and_commit",
            "generate_image",
            "search_transcript",
            "view_transcript",
        ]
        # Fetch runtime context length from nixx-server /health (auto-fetched from llama.cpp
        # at startup, so more accurate than cfg.llm_context_length which is the .env default)
        runtime_ctx = cfg.llm_context_length
        try:
            async with httpx.AsyncClient(timeout=1.0) as client:
                hr = await client.get("http://127.0.0.1:8000/health")
                if hr.status_code == 200:
                    runtime_ctx = int(hr.json().get("context_length", runtime_ctx))
        except Exception:
            pass
        image_health: dict[str, Any] = {}
        try:
            async with httpx.AsyncClient(timeout=2.0) as _c:
                _hr = await _c.get("http://127.0.0.1:8090/health")
                if _hr.status_code == 200:
                    image_health = _hr.json()
        except Exception:
            pass
        return {
            "model": cfg.llm_model,
            "context_length": runtime_ctx,
            "summary_interval": cfg.summary_interval,
            "intent_interval": cfg.intent_interval,
            "intent": intent,
            "tools": tools,
            "paths": {
                "source": str(cfg.source_dir),
                "scratch": str(cfg.scratch_dir),
                "project": project_dir,
            },
            "image_models": {
                "generate": [
                    {"id": "sd14", "label": "SD 1.4", "hardware": "GPU only", "size": "~2 GiB"},
                    {"id": "sd21", "label": "SD 2.1", "hardware": "GPU only", "size": "~3.5 GiB"},
                    {
                        "id": "sdxl",
                        "label": "SDXL",
                        "hardware": "model offload",
                        "size": "~6.9 GiB",
                    },
                    {
                        "id": "sdxl_turbo",
                        "label": "SDXL Turbo",
                        "hardware": "model offload",
                        "size": "~6.9 GiB",
                    },
                ],
                "active_generate": image_health.get("active_generate_model", "sd21"),
            },
        }

    # ── Dashboard HTML ────────────────────────────────────────────────────────

    _HTML = (Path(__file__).parent / "admin_web" / "index.html").read_text()

    @app.get("/admin", response_class=HTMLResponse)
    @app.get("/admin/", response_class=HTMLResponse)
    async def dashboard() -> HTMLResponse:
        return HTMLResponse(_HTML)

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(
        "nixx.admin:create_app",
        factory=True,
        host="0.0.0.0",
        port=8001,
        log_level="info",
    )


if __name__ == "__main__":
    main()
