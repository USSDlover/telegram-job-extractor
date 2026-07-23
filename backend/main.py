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
    discover_channels_categories,
    normalize_channels,
    scrape_and_process_channels,
)
from storage import get_distinct_categories, get_jobs_sorted

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
        return await discover_channels_categories(channels)
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
    await broadcast_log(
        "EXTRACTION_QUEUED",
        f"Extraction queued for {len(channels)} channel(s).",
        {
            "channels": channels,
            "selected_categories": body.selected_categories,
            "selected_titles": body.selected_titles,
        },
    )
    background_tasks.add_task(
        scrape_and_process_channels,
        channels,
        body.selected_categories,
        body.selected_titles,
    )
    return {
        "status": "started",
        "channels": channels,
        "message": f"Extraction pipeline started for {len(channels)} channel(s)",
    }


@app.get("/api/jobs")
async def list_jobs(
    category: Optional[str] = Query(default=None),
    sort_by: str = Query(default="date_desc"),
):
    jobs = await get_jobs_sorted(category=category, sort_by=sort_by)
    return {"jobs": jobs, "total": len(jobs), "sort_by": sort_by}


@app.get("/api/categories")
async def list_categories():
    categories = await get_distinct_categories()
    return {"categories": categories}


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
