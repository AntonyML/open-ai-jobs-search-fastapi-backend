"""In-process ranking job coordinator."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rank import execute_rank

_jobs: dict[str, dict[str, Any]] = {}

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

async def start(db_factory, user_id: str, payload: dict[str, Any]) -> str:
    job_id = f"{user_id}:{int(datetime.now().timestamp() * 1000)}"
    state = {"id": job_id, "status": "running", "provider": None, "model": None,
             "started_at": _now(), "finished_at": None, "error": None}
    _jobs[job_id] = state

    async def run() -> None:
        try:
            async with db_factory() as db:
                result = await execute_rank(db, user_id, **payload)
                state.update({"status": "completed", "result": result.model_dump()})
        except asyncio.CancelledError:
            state["status"] = "cancelled"
        except Exception as exc:
            state.update({"status": "failed", "error": str(exc)})
        finally:
            state["finished_at"] = _now()

    state["task"] = asyncio.create_task(run())
    return job_id

def get(job_id: str) -> dict[str, Any] | None:
    state = _jobs.get(job_id)
    if not state: return None
    return {k: v for k, v in state.items() if k != "task"}

async def cancel(job_id: str) -> bool:
    state = _jobs.get(job_id)
    if not state or state["status"] != "running": return False
    state["task"].cancel()
    return True
