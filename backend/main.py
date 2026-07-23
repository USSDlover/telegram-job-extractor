"""FastAPI entry point: API routes + static React UI."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config import settings
from scraper import discover_channel_categories, scrape_and_process_channel
from storage import get_distinct_categories, get_jobs_sorted

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class DiscoverRequest(BaseModel):
    channel: str = Field(..., examples=["@tech_jobs_channel"])


class ExtractRequest(BaseModel):
    channel: str
    selected_categories: List[str] = Field(default_factory=list)
    selected_titles: List[str] = Field(default_factory=list)


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
    try:
        result = await discover_channel_categories(body.channel)
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
    if not body.channel.strip():
        raise HTTPException(status_code=400, detail="channel is required")
    background_tasks.add_task(
        scrape_and_process_channel,
        body.channel,
        body.selected_categories,
        body.selected_titles,
    )
    return {
        "status": "started",
        "channel": body.channel,
        "message": "Extraction pipeline started in background",
    }


@app.get("/api/jobs")
async def list_jobs(category: Optional[str] = Query(default=None)):
    jobs = await get_jobs_sorted(category=category)
    return {"jobs": jobs, "total": len(jobs)}


@app.get("/api/categories")
async def list_categories():
    categories = await get_distinct_categories()
    return {"categories": categories}


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
        # Never hijack API (already registered above)
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
