"""FastAPI entry point: API routes, SSE activity stream, static React UI."""

from __future__ import annotations

import asyncio
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
from ai_extractor import normalize_publish_language
from publisher import (
    fetch_channel_stats,
    get_telegram_session_status,
    list_admin_channels,
    publish_job_to_channels,
    publish_pending_jobs,
    resolve_publish_targets,
    send_announcement,
)
from scraper import (
    normalize_channels,
    request_stop_extraction,
    reset_stop_flag,
    sample_channel_categories,
    scrape_and_process_channels,
)
from storage import (
    add_admin_channel,
    add_scraper_channel,
    clear_all_jobs,
    delete_admin_channel,
    delete_scraper_channel,
    delete_job,
    delete_jobs_bulk,
    get_admin_channels,
    get_distinct_categories,
    get_job_by_id,
    get_jobs_by_ids,
    get_jobs_sorted,
    get_published_jobs,
    get_scraper_channels,
    get_unpublished_jobs,
    mark_job_as_published,
    read_debug_samples,
    resolve_date_bounds,
    set_channel_language,
    toggle_default_channel,
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
    settings.channels_file.parent.mkdir(parents=True, exist_ok=True)
    if not settings.channels_file.exists():
        settings.channels_file.write_text("[]", encoding="utf-8")
    settings.scraper_channels_file.parent.mkdir(parents=True, exist_ok=True)
    if not settings.scraper_channels_file.exists():
        settings.scraper_channels_file.write_text("[]", encoding="utf-8")
    logger.info("Jobs store: %s", settings.jobs_file)
    logger.info("Channels store: %s", settings.channels_file)
    logger.info("Scraper channels store: %s", settings.scraper_channels_file)
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


class BulkDeleteRequest(BaseModel):
    job_ids: List[str] = Field(default_factory=list)


@app.delete("/api/jobs/clear-all")
async def clear_jobs():
    """Wipe all stored jobs (jobs.json → []). Must be registered before /{job_id}."""
    removed = await clear_all_jobs()
    await broadcast_log(
        "JOBS_CLEARED",
        f"All jobs cleared successfully ({removed} removed).",
        {"removed": removed},
    )
    return {
        "success": True,
        "message": "All jobs cleared successfully",
        "removed": removed,
    }


@app.post("/api/jobs/bulk-delete")
async def bulk_delete_jobs(payload: BulkDeleteRequest):
    """Delete multiple jobs by id (or message_id). Registered before /{job_id}."""
    ids = [str(j).strip() for j in (payload.job_ids or []) if str(j).strip()]
    if not ids:
        raise HTTPException(status_code=400, detail="job_ids must be a non-empty list")
    result = await delete_jobs_bulk(ids)
    await broadcast_log(
        "JOBS_BULK_DELETED",
        f"Bulk deleted {result['deleted_count']} job(s).",
        {
            "deleted_count": result["deleted_count"],
            "deleted_ids": result["deleted_ids"],
            "not_found": result["not_found"],
        },
    )
    return {
        "success": True,
        "deleted_count": result["deleted_count"],
        "deleted_ids": result["deleted_ids"],
        "not_found": result["not_found"],
        "message": f"Deleted {result['deleted_count']} job(s)",
    }


class ChannelCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, examples=["Main Channel"])
    handle: str = Field(..., min_length=1, examples=["@huntjobarmenia"])
    default_language: str = Field(default="English", examples=["English", "Persian", "Arabic"])


class ChannelLanguageRequest(BaseModel):
    default_language: str = Field(..., examples=["Persian"])


class ScraperChannelCreateRequest(BaseModel):
    handle: str = Field(..., min_length=1, examples=["@job_am"])
    name: Optional[str] = Field(default=None, examples=["Job.am"])


class PublishTargetRequest(BaseModel):
    target_channels: List[str] = Field(
        default_factory=list,
        description="Destination channels; defaults to is_default channels in channels.json.",
        examples=[["@huntjobarmenia", "@expatjobs_am"]],
    )
    target_channel: Optional[str] = Field(
        default=None,
        description="Single destination; merged into target_channels when present.",
        examples=["@huntjobarmenia"],
    )
    channel_username: Optional[str] = Field(
        default=None,
        description="Legacy alias for target_channel.",
    )
    delay_seconds: Optional[float] = Field(
        default=None,
        ge=0,
        le=60,
        description="Override delay between posts for publish-all-pending.",
    )
    language: str = Field(
        default="English",
        description="Publish language: English, Persian, or Arabic.",
        examples=["English", "Persian", "Arabic"],
    )
    republish: bool = Field(
        default=False,
        description="When true, re-send a job that is already published_to_telegram.",
    )
    job_ids: List[str] = Field(
        default_factory=list,
        description="Optional job ids for publish-selected / filtered batch publish.",
    )


async def _targets_from_request(
    payload: Optional[PublishTargetRequest],
    query_channel: Optional[str],
) -> list[str]:
    raw: list[str] = []
    if payload is not None:
        raw.extend(payload.target_channels or [])
        if payload.target_channel:
            raw.append(payload.target_channel)
        if payload.channel_username:
            raw.append(payload.channel_username)
    if query_channel:
        raw.append(query_channel)
    try:
        return await resolve_publish_targets(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _language_from_request(payload: Optional[PublishTargetRequest]) -> str:
    raw = payload.language if payload is not None else "English"
    return normalize_publish_language(raw)


@app.get("/api/channels")
async def list_admin_target_channels():
    """Saved destination channels from channels.json (not Telethon discovery)."""
    channels = await get_admin_channels()
    return {"channels": channels, "total": len(channels)}


@app.post("/api/channels")
async def create_admin_target_channel(body: ChannelCreateRequest):
    """Add a destination channel. Handle is cleaned and prefixed with @."""
    try:
        channel = await add_admin_channel(
            body.name,
            body.handle,
            default_language=body.default_language,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    channels = await get_admin_channels()
    return {"channel": channel, "channels": channels, "total": len(channels)}


@app.delete("/api/channels/{channel_id}")
async def remove_admin_target_channel(channel_id: str):
    deleted = await delete_admin_channel(channel_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Channel not found: {channel_id}")
    return {"success": True, "deleted_id": channel_id}


@app.patch("/api/channels/{channel_id}/default")
async def patch_default_admin_channel(channel_id: str):
    """Toggle the is_default flag on a saved destination channel."""
    channel = await toggle_default_channel(channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail=f"Channel not found: {channel_id}")
    channels = await get_admin_channels()
    return {"channel": channel, "channels": channels, "total": len(channels)}


@app.patch("/api/channels/{channel_id}/language")
async def patch_admin_channel_language(channel_id: str, body: ChannelLanguageRequest):
    """Set the default publish language for a saved destination channel."""
    channel = await set_channel_language(channel_id, body.default_language)
    if channel is None:
        raise HTTPException(status_code=404, detail=f"Channel not found: {channel_id}")
    channels = await get_admin_channels()
    return {"channel": channel, "channels": channels, "total": len(channels)}


@app.get("/api/scraper-channels")
async def list_scraper_source_channels():
    """Saved source channels used for discovery and extraction."""
    channels = await get_scraper_channels()
    return {"channels": channels, "total": len(channels)}


@app.post("/api/scraper-channels")
async def create_scraper_source_channel(body: ScraperChannelCreateRequest):
    """Add a source scrape channel. Handle is cleaned and prefixed with @."""
    try:
        channel = await add_scraper_channel(body.handle, body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    channels = await get_scraper_channels()
    return {"channel": channel, "channels": channels, "total": len(channels)}


@app.delete("/api/scraper-channels/{channel_id}")
async def remove_scraper_source_channel(channel_id: str):
    deleted = await delete_scraper_channel(channel_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Source channel not found: {channel_id}")
    return {"success": True, "deleted_id": channel_id}


@app.post("/api/jobs/publish-all-pending")
async def publish_all_pending(
    background_tasks: BackgroundTasks,
    payload: Optional[PublishTargetRequest] = None,
    channel_username: Optional[str] = Query(
        default=None,
        description="Destination channel; defaults to saved is_default channels.",
    ),
    delay_seconds: Optional[float] = Query(
        default=None,
        ge=0,
        le=60,
        description="Seconds between posts; defaults to TELEGRAM_PUBLISH_DELAY.",
    ),
):
    """Queue sequential publishing of every unpublished job."""
    targets = await _targets_from_request(payload, channel_username)
    language = _language_from_request(payload)
    delay = delay_seconds
    if payload is not None and payload.delay_seconds is not None:
        delay = payload.delay_seconds
    label = ", ".join(targets)
    pending = await get_unpublished_jobs()
    if not pending:
        await broadcast_log(
            "PUBLISH_BATCH_DONE",
            f"No unpublished jobs to post to {label}.",
            {"channels": targets, "pending": 0, "language": language},
        )
        return {
            "status": "idle",
            "channels": targets,
            "channel": targets[0] if targets else None,
            "pending": 0,
            "language": language,
            "message": "No unpublished jobs",
        }

    await broadcast_log(
        "PUBLISH_BATCH_QUEUED",
        f"Queued {len(pending)} unpublished job(s) for {len(targets)} channel(s) ({label}) in {language}.",
        {
            "channels": targets,
            "pending": len(pending),
            "delay_seconds": delay,
            "language": language,
        },
    )
    background_tasks.add_task(
        publish_pending_jobs,
        delay_seconds=delay,
        target_channels=targets,
        language=language,
    )
    return {
        "status": "started",
        "channels": targets,
        "channel": targets[0] if targets else None,
        "pending": len(pending),
        "language": language,
        "delay_seconds": delay if delay is not None else settings.telegram_publish_delay,
        "message": (
            f"Publishing {len(pending)} job(s) to {len(targets)} channel(s) ({label}) in {language}"
        ),
    }


@app.post("/api/jobs/publish-selected")
async def publish_selected_jobs(
    background_tasks: BackgroundTasks,
    payload: PublishTargetRequest,
    channel_username: Optional[str] = Query(
        default=None,
        description="Destination channel; defaults to saved is_default channels.",
    ),
):
    """Queue publishing for an explicit list of job ids (pending and/or republish)."""
    job_ids = [str(item).strip() for item in (payload.job_ids or []) if str(item).strip()]
    if not job_ids:
        raise HTTPException(status_code=400, detail="job_ids must be a non-empty list")

    targets = await _targets_from_request(payload, channel_username)
    language = _language_from_request(payload)
    republish = bool(payload.republish)
    delay = payload.delay_seconds
    resolved = await get_jobs_by_ids(job_ids)
    selected = list(resolved.get("jobs") or [])
    if not republish:
        selected = [job for job in selected if not job.get("published_to_telegram")]
    label = ", ".join(targets)

    if not selected:
        await broadcast_log(
            "PUBLISH_BATCH_DONE",
            "No selected jobs to publish"
            + (" (already published; set republish=true to send again)." if not republish else "."),
            {
                "channels": targets,
                "pending": 0,
                "language": language,
                "job_ids": job_ids,
                "not_found": resolved.get("not_found") or [],
                "republish": republish,
            },
        )
        return {
            "status": "idle",
            "channels": targets,
            "channel": targets[0] if targets else None,
            "pending": 0,
            "selected": 0,
            "not_found": resolved.get("not_found") or [],
            "language": language,
            "republish": republish,
            "message": "No selected jobs to publish",
        }

    verb = "Republishing" if republish else "Publishing"
    await broadcast_log(
        "PUBLISH_BATCH_QUEUED",
        f"Queued {len(selected)} selected job(s) for {len(targets)} channel(s) ({label}) in {language}.",
        {
            "channels": targets,
            "pending": len(selected),
            "language": language,
            "job_ids": [str(job.get("id") or job.get("message_id") or "") for job in selected],
            "republish": republish,
        },
    )
    background_tasks.add_task(
        publish_pending_jobs,
        delay_seconds=delay,
        target_channels=targets,
        language=language,
        job_ids=job_ids,
        republish=republish,
    )
    return {
        "status": "started",
        "channels": targets,
        "channel": targets[0] if targets else None,
        "pending": len(selected),
        "selected": len(selected),
        "not_found": resolved.get("not_found") or [],
        "language": language,
        "republish": republish,
        "delay_seconds": delay if delay is not None else settings.telegram_publish_delay,
        "message": (
            f"{verb} {len(selected)} selected job(s) to {len(targets)} channel"
            f"{'' if len(targets) == 1 else 's'} ({label}) in {language}"
        ),
    }


@app.post("/api/jobs/{job_id}/publish")
async def publish_one_job(
    job_id: str,
    payload: Optional[PublishTargetRequest] = None,
    channel_username: Optional[str] = Query(
        default=None,
        description="Destination channel; defaults to saved is_default channels.",
    ),
):
    """Publish a single stored job to one or more destination Telegram channels."""
    targets = await _targets_from_request(payload, channel_username)
    language = _language_from_request(payload)
    republish = bool(payload.republish) if payload is not None else False
    job = await get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    resolved_id = str(job.get("id") or job_id)
    if job.get("published_to_telegram") and not republish:
        raise HTTPException(
            status_code=400,
            detail="Job is already published. Set republish=true to send it again.",
        )

    action = "Republishing" if republish else "Publishing"
    try:
        result = await publish_job_to_channels(
            job,
            targets,
            language=language,
            republish=republish,
        )
        if not result.get("ok"):
            detail = "; ".join(result.get("failed") or ["All destination channels failed"])
            raise RuntimeError(detail)
        marked = await mark_job_as_published(
            resolved_id,
            extra={
                "published_url": result.get("posted_url"),
                "published_message_id": result.get("posted_message_id"),
                "published_channels": result.get("channels"),
                "published_language": language,
            },
        )
        ok_channels = result.get("channels") or targets
        history = (marked or {}).get("publication_history") or []
        verb = "Republished" if republish else "Published"
        return {
            "status": "republished" if republish else "published",
            "job_id": resolved_id,
            "channels": ok_channels,
            "channel": ok_channels[0] if ok_channels else None,
            "posted_message_id": result.get("posted_message_id"),
            "posted_url": result.get("posted_url"),
            "failed": result.get("failed") or [],
            "published_to_telegram": True,
            "publication_history": history,
            "language": language,
            "republish": republish,
            "message": (
                f"{verb} job {resolved_id} to {len(ok_channels)} channel"
                f"{'' if len(ok_channels) == 1 else 's'} ({', '.join(ok_channels)}) in {language}"
            ),
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("%s failed for %s", action, resolved_id)
        await broadcast_log(
            "ERROR",
            f"Failed to {'republish' if republish else 'publish'} job {resolved_id}: {exc}",
            {"job_id": resolved_id, "channels": targets, "republish": republish},
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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


class BroadcastRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    target_channels: List[str] = Field(default_factory=list)
    target_channel: Optional[str] = None
    channel_username: Optional[str] = None


@app.get("/api/telegram/status")
async def telegram_status():
    """Navbar session health + configured target channel."""
    return await get_telegram_session_status()


@app.get("/api/telegram/admin-channels")
async def telegram_admin_channels(force: bool = Query(default=False)):
    """Channels where the logged-in account can post as owner or admin."""
    try:
        channels = await list_admin_channels(force=force)
        return {"channels": channels, "total": len(channels)}
    except Exception as exc:
        logger.exception("Admin channel listing failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/telegram/stats")
async def telegram_stats(force: bool = Query(default=False)):
    """Live channel analytics, with stored job counts as a graceful fallback."""
    stats = await fetch_channel_stats(force=force)
    recent = await get_published_jobs()
    stats["recent_published"] = recent[:25]
    return stats


@app.post("/api/telegram/broadcast")
async def telegram_broadcast(body: BroadcastRequest):
    """Send a custom Markdown announcement to one or more target channels."""
    raw = list(body.target_channels or [])
    if body.target_channel:
        raw.append(body.target_channel)
    if body.channel_username:
        raw.append(body.channel_username)
    try:
        targets = await resolve_publish_targets(raw)
        posted: list[dict] = []
        failed: list[str] = []
        for index, channel in enumerate(targets):
            try:
                posted.append(await send_announcement(body.message, channel))
            except Exception as exc:
                failed.append(f"{channel}: {exc}")
                await broadcast_log(
                    "ERROR",
                    f"Broadcast failed for {channel}: {exc}",
                    {"channel": channel},
                )
            if index < len(targets) - 1:
                await asyncio.sleep(max(0.0, settings.telegram_channel_delay))
        if not posted:
            raise RuntimeError("; ".join(failed) or "Broadcast failed")
        ok = [item.get("channel") for item in posted if item.get("channel")]
        return {
            "status": "sent",
            "channels": ok,
            "channel": ok[0] if ok else None,
            "posted": posted,
            "failed": failed,
            "posted_url": posted[0].get("posted_url") if posted else "",
            "message": f"Posted to {len(ok)} channel{'' if len(ok) == 1 else 's'} ({', '.join(ok)})",
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Broadcast failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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
