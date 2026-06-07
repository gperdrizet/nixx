"""Standalone admin dashboard for nixx.

Runs on port 8001, binds to Tailscale IP only. No nixx imports - reads DB
and calls systemctl directly. Start with: nixx-admin (entry point) or
uvicorn nixx.admin:create_app --factory --host 100.64.0.2 --port 8001
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import asyncpg
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse


def create_app() -> FastAPI:
    app = FastAPI(title="nixx admin", docs_url=None, redoc_url=None)

    # ── DB connection (lazy, per-request) ─────────────────────────────────────

    async def _db() -> asyncpg.Connection:
        from nixx.config import NixxConfig  # only for DATABASE_URL

        cfg = NixxConfig()
        return await asyncpg.connect(cfg.database_url)

    # ── Service status ────────────────────────────────────────────────────────

    def _service_status(name: str) -> dict[str, str]:
        try:
            r = subprocess.run(
                ["systemctl", "is-active", name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            state = r.stdout.strip()
        except Exception:
            state = "unknown"
        return {"name": name, "state": state}

    # ── Routes ────────────────────────────────────────────────────────────────

    @app.get("/admin/api/status")
    async def get_status() -> dict[str, Any]:
        def _svc(display_name: str, unit: str) -> dict[str, str]:
            s = _service_status(unit)
            s["name"] = display_name
            return s

        services = [
            _service_status("nixx-server"),
            _service_status("nixx-embed"),
            _svc("llamacpp", "llamacpp.service"),
            _svc("postgresql", "docker"),
        ]
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
                    (SELECT COALESCE(SUM(tool_calls_made), 0) FROM buffer WHERE role = 'assistant') AS total_tool_calls
                """)
            db = dict(rows[0])
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
            }
        finally:
            await conn.close()

    @app.post("/admin/api/restart/{service}")
    async def restart_service(service: str) -> dict[str, str]:
        allowed = {"nixx-server", "nixx-embed"}
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
            "search_transcript",
            "view_transcript",
        ]
        return {
            "model": cfg.llm_model,
            "context_length": cfg.llm_context_length,
            "summary_interval": cfg.summary_interval,
            "intent_interval": cfg.intent_interval,
            "intent": intent,
            "tools": tools,
            "paths": {
                "source": str(cfg.source_dir),
                "scratch": str(cfg.scratch_dir),
                "project": project_dir,
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

    # Bind to Tailscale IP only
    uvicorn.run(
        "nixx.admin:create_app",
        factory=True,
        host="100.64.0.2",
        port=8001,
        log_level="info",
    )


if __name__ == "__main__":
    main()
