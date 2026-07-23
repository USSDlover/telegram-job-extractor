"""FastAPI entry point: API routes, SSE activity stream, static React UI."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from activity_log import broadcast_log, sse_event_stream, subscribe
from config import settings
from scraper import (
    normalize_channels,
    request_stop_extraction,
    reset_stop_flag,
    sample_channel_categories,
    scrape_and_process_channels,
)
from storage import (
    delete_job,
    get_distinct_categories,
    get_jobs_sorted,
    read_debug_samples,
    resolve_date_bounds,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class DiscoverRequest(BaseModel):
    channels: List[str] = Field(default_factory=list, examples=[["@tech_jobs", "@remote_work"]])
    channel: Optional[str] = Field(
        default=None,
        description="Legacy single-channel field; merged into channels when present.",
    )

    @model_validator(mode="after")
    def require_channels(self) -> "DiscoverRequest":
        try:
            normalize_channels(self.channels, self.channel)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return self


class ExtractRequest(BaseModel):
    channels: List[str] = Field(default_factory=list)
    channel: Optional[str] = None
    selected_categories: List[str] = Field(default_factory=list)
    selected_titles: List[str] = Field(default_factory=list)
    date_preset: Optional[str] = Field(
        default="today",
        description="today | this_week | this_month | all_time | custom",
    )
    start_date: Optional[str] = Field(default=None, description="YYYY-MM-DD")
    end_date: Optional[str] = Field(default=None, description="YYYY-MM-DD")

    @model_validator(mode="after")
    def require_channels(self) -> "ExtractRequest":
        try:
            normalize_channels(self.channels, self.channel)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return self


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.jobs_file.parent.mkdir(parents=True, exist_ok=True)
    if not settings.jobs_file.exists():
        settings.jobs_file.write_text("[]", encoding="utf-8")
    logger.info("Jobs store: %s", settings.jobs_file)
    yield


app = FastAPI(title="Telegram Job Extractor", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/api/discover")
async def discover(body: DiscoverRequest):
    channels = normalize_channels(body.channels, body.channel)
    await broadcast_log(
        "DISCOVER_STARTED",
        f"Discovery requested for {len(channels)} channel(s)...",
        {"channels": channels},
    )
    try:
        result = await sample_channel_categories(channels, sample_limit_per_channel=15)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Discover failed")
        raise HTTPException(status_code=502, detail=f"Discovery failed: {exc}") from exc


@app.post("/api/extract", status_code=202)
async def extract(body: ExtractRequest, background_tasks: BackgroundTasks):
    channels = normalize_channels(body.channels, body.channel)
    start_dt, end_dt = resolve_date_bounds(
        body.date_preset,
        body.start_date,
        body.end_date,
    )
    reset_stop_flag()
    await broadcast_log(
        "EXTRACTION_QUEUED",
        f"Extraction queued for {len(channels)} channel(s)"
        + (
            f" between {start_dt.date().isoformat() if start_dt else '…'} "
            f"and {end_dt.date().isoformat() if end_dt else '…'}"
            if start_dt or end_dt
            else " (all time)"
        )
        + ".",
        {
            "channels": channels,
            "selected_categories": body.selected_categories,
            "selected_titles": body.selected_titles,
            "date_preset": body.date_preset,
            "start_date": start_dt.isoformat() if start_dt else None,
            "end_date": end_dt.isoformat() if end_dt else None,
        },
    )
    background_tasks.add_task(
        scrape_and_process_channels,
        channels,
        body.selected_categories,
        body.selected_titles,
        start_dt,
        end_dt,
    )
    return {
        "status": "started",
        "channels": channels,
        "date_preset": body.date_preset or "today",
        "start_date": start_dt.isoformat() if start_dt else None,
        "end_date": end_dt.isoformat() if end_dt else None,
        "message": f"Extraction pipeline started for {len(channels)} channel(s)",
    }


@app.post("/api/stop-extraction")
async def stop_extraction():
    """Request cooperative cancellation of the active extraction pipeline."""
    newly = request_stop_extraction()
    await broadcast_log(
        "EXTRACTION_STOP_REQUESTED",
        "User requested task termination. Stop signal received. Halting extraction...",
        {"newly_requested": newly},
    )
    return {
        "status": "stopping" if newly else "already_stopping",
        "message": "Stop signal sent to scraper",
    }


@app.get("/api/jobs")
async def list_jobs(
    category: Optional[str] = Query(default=None),
    sort_by: str = Query(default="date_desc"),
    preset: Optional[str] = Query(
        default="all_time",
        description="today | this_week | this_month | all_time | custom",
    ),
    start_date: Optional[str] = Query(default=None, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(default=None, description="YYYY-MM-DD"),
):
    jobs = await get_jobs_sorted(
        category=category,
        sort_by=sort_by,
        preset=preset,
        start_date=start_date,
        end_date=end_date,
    )
    return {
        "jobs": jobs,
        "total": len(jobs),
        "sort_by": sort_by,
        "preset": preset or "all_time",
        "start_date": start_date,
        "end_date": end_date,
    }


@app.delete("/api/jobs/{job_id}")
async def remove_job(job_id: str):
    """Delete a stored job by unique id (e.g. channel_msgid) or message_id."""
    deleted = await delete_job(job_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return {"success": True, "deleted_id": job_id}


@app.get("/api/categories")
async def list_categories():
    categories = await get_distinct_categories()
    return {"categories": categories}


@app.get("/api/debug/samples")
async def debug_samples(limit: int = Query(default=20, ge=1, le=50)):
    """Inspect recent discovery LLM inputs/outputs saved to debug_samples.json."""
    samples = await read_debug_samples(limit=limit)
    return {"samples": samples, "total": len(samples)}


@app.get("/api/stream-logs")
async def stream_logs(request: Request):
    """Server-Sent Events stream of pipeline activity logs."""
    queue = await subscribe()

    async def event_generator():
        async for chunk in sse_event_stream(queue):
            if await request.is_disconnected():
                break
            yield chunk

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# --- Static frontend (production build) ---
_dist = settings.frontend_dist
if _dist.is_dir():
    assets_dir = _dist / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        candidate = _dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        index = _dist / "index.html"
        if index.is_file():
            return FileResponse(index)
        raise HTTPException(status_code=404, detail="Frontend not built")
else:
    logger.warning(
        "Frontend dist not found at %s — run `npm run build` in frontend/",
        _dist,
    )
