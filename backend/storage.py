"""Async JSON persistence for extracted jobs, admin channels, scraper sources, and debug samples."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import aiofiles

from config import settings

_lock = asyncio.Lock()
_debug_lock = asyncio.Lock()
_channels_lock = asyncio.Lock()
_scraper_lock = asyncio.Lock()
_PUNCT_TRIM = re.compile(r"^[\s\-–—|:;,.]+|[\s\-–—|:;,.]+$")
_CHANNEL_ID_RE = re.compile(r"^c(\d+)$")
_SCRAPER_CHANNEL_ID_RE = re.compile(r"^s(\d+)$")


def normalize_label(value: str | None) -> str:
    """Trim whitespace/punctuation and collapse internal spaces."""
    if not value:
        return ""
    text = _PUNCT_TRIM.sub("", str(value).strip())
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_label_list(values: Iterable[str] | None) -> list[str]:
    """
    Case-insensitive dedupe with punctuation/whitespace cleanup.
    Prefers the first non-empty casing seen for each key.
    """
    if not values:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        label = normalize_label(raw)
        if not label:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        # Prefer Title Case when the model returns all-lowercase duplicates later
        if label.islower() and " " in label:
            label = label.title()
        out.append(label)
    return out


def _ensure_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("[]", encoding="utf-8")


def _normalize_publication_history(raw: Any) -> list[dict[str, Any]]:
    """Keep only well-formed publication_history entries."""
    if not isinstance(raw, list):
        return []
    history: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        channel = str(item.get("channel") or "").strip()
        language = str(item.get("language") or "English").strip() or "English"
        published_at = str(item.get("published_at") or "").strip()
        if not channel and not published_at:
            continue
        history.append(
            {
                "channel": channel,
                "language": language,
                "published_at": published_at,
            }
        )
    return history


def _publication_entries(
    channels: Iterable[str] | None,
    language: str,
    published_at: str,
) -> list[dict[str, Any]]:
    """One history row per destination for a single publish attempt."""
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    lang = str(language or "English").strip() or "English"
    for raw in channels or []:
        channel = str(raw or "").strip()
        if not channel:
            continue
        key = channel.casefold()
        if key in seen:
            continue
        seen.add(key)
        entries.append(
            {
                "channel": channel,
                "language": lang,
                "published_at": published_at,
            }
        )
    return entries


def _apply_job_defaults(job: dict[str, Any]) -> dict[str, Any]:
    """Ensure persisted records always expose publication state."""
    record = dict(job)
    record["published_to_telegram"] = bool(record.get("published_to_telegram", False))
    record["publication_history"] = _normalize_publication_history(
        record.get("publication_history")
    )
    return record


def _job_id_matches(job: dict[str, Any], needle: str) -> bool:
    return str(job.get("id") or "") == needle or str(job.get("message_id") or "") == needle


async def _read_unlocked() -> list[dict[str, Any]]:
    path = settings.jobs_file
    _ensure_file(path)
    async with aiofiles.open(path, "r", encoding="utf-8") as f:
        raw = await f.read()
    try:
        data = json.loads(raw or "[]")
        if not isinstance(data, list):
            return []
        return [_apply_job_defaults(j) for j in data if isinstance(j, dict)]
    except json.JSONDecodeError:
        return []


async def _write_unlocked(jobs: list[dict[str, Any]]) -> None:
    path = settings.jobs_file
    _ensure_file(path)
    payload = json.dumps(jobs, ensure_ascii=False, indent=2)
    tmp = path.with_suffix(".json.tmp")
    async with aiofiles.open(tmp, "w", encoding="utf-8") as f:
        await f.write(payload)
    tmp.replace(path)


async def read_jobs() -> list[dict[str, Any]]:
    async with _lock:
        return await _read_unlocked()


async def write_jobs(jobs: list[dict[str, Any]]) -> None:
    async with _lock:
        await _write_unlocked(jobs)


async def upsert_jobs(new_jobs: list[dict[str, Any]]) -> int:
    """Merge jobs by id; returns count of newly inserted records."""
    if not new_jobs:
        return 0
    async with _lock:
        existing = await _read_unlocked()
        by_id = {j.get("id"): j for j in existing if j.get("id")}
        inserted = 0
        for incoming in new_jobs:
            job = dict(incoming)
            job_id = job.get("id")
            if not job_id:
                continue
            previous = by_id.get(job_id)
            if previous is None:
                inserted += 1
                job.setdefault("published_to_telegram", False)
                job.setdefault("publication_history", [])
            elif previous.get("published_to_telegram"):
                # Re-extraction must not un-publish an already posted job
                job["published_to_telegram"] = True
                for key in (
                    "published_at",
                    "published_url",
                    "published_message_id",
                    "published_channels",
                    "published_language",
                    "publication_history",
                ):
                    if previous.get(key) and not job.get(key):
                        job[key] = previous[key]
            else:
                job.setdefault("published_to_telegram", False)
                if previous.get("publication_history") and not job.get("publication_history"):
                    job["publication_history"] = previous["publication_history"]
            by_id[job_id] = _apply_job_defaults(job)
        await _write_unlocked(list(by_id.values()))
        return inserted


async def get_job_by_id(job_id: str) -> dict[str, Any] | None:
    """Return a job by unique `id` or `message_id`, or None."""
    needle = (job_id or "").strip()
    if not needle:
        return None
    for job in await read_jobs():
        if _job_id_matches(job, needle):
            return job
    return None


async def get_jobs_by_ids(job_ids: Iterable[str]) -> dict[str, Any]:
    """
    Resolve jobs by unique `id` or `message_id`, preserving request order.
    Returns {jobs, found_ids, not_found}.
    """
    needles = [str(j).strip() for j in (job_ids or []) if str(j).strip()]
    if not needles:
        return {"jobs": [], "found_ids": [], "not_found": []}

    by_key: dict[str, dict[str, Any]] = {}
    for job in await read_jobs():
        jid = str(job.get("id") or "").strip()
        mid = str(job.get("message_id") or "").strip()
        if jid:
            by_key[jid] = job
        if mid:
            by_key.setdefault(mid, job)

    jobs: list[dict[str, Any]] = []
    found_ids: list[str] = []
    not_found: list[str] = []
    seen: set[str] = set()
    for needle in needles:
        job = by_key.get(needle)
        if job is None:
            not_found.append(needle)
            continue
        resolved = str(job.get("id") or job.get("message_id") or needle)
        if resolved in seen:
            continue
        seen.add(resolved)
        jobs.append(job)
        found_ids.append(resolved)
    return {"jobs": jobs, "found_ids": found_ids, "not_found": not_found}


async def get_unpublished_jobs() -> list[dict[str, Any]]:
    """Jobs that have not been posted to the destination channel."""
    return [j for j in await read_jobs() if not j.get("published_to_telegram")]


async def get_published_jobs() -> list[dict[str, Any]]:
    """Jobs already posted, newest publication first."""
    jobs = [j for j in await read_jobs() if j.get("published_to_telegram")]
    return sorted(
        jobs,
        key=lambda j: str(j.get("published_at") or j.get("date") or ""),
        reverse=True,
    )


async def mark_job_as_published(
    job_id: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    Atomically set published_to_telegram=True for a job and append
    publication_history. Matches unique `id` or `message_id`.
    Returns the updated record, or None if no job matched.
    Optional extra fields (published_url, published_message_id, …) are merged in.
    """
    needle = (job_id or "").strip()
    if not needle:
        return None
    extra = dict(extra or {})
    published_at = datetime.now(timezone.utc).isoformat()
    history_channels = extra.pop("published_channels", None)
    history_language = extra.pop("published_language", None)
    async with _lock:
        existing = await _read_unlocked()
        updated: dict[str, Any] | None = None
        for job in existing:
            if _job_id_matches(job, needle):
                job["published_to_telegram"] = True
                job["published_at"] = published_at
                if history_channels:
                    job["published_channels"] = list(history_channels)
                if history_language:
                    job["published_language"] = history_language
                for key, value in extra.items():
                    if value is not None and value != "":
                        job[key] = value
                entries = _publication_entries(
                    history_channels,
                    str(history_language or extra.get("language") or "English"),
                    published_at,
                )
                history = _normalize_publication_history(job.get("publication_history"))
                history.extend(entries)
                job["publication_history"] = history
                updated = dict(job)
                break
        if updated is not None:
            await _write_unlocked(existing)
        return updated


async def delete_job(job_id: str) -> bool:
    """
    Remove a job by its unique `id` (preferred) or message_id string match.
    Returns True if a record was deleted.
    """
    needle = (job_id or "").strip()
    if not needle:
        return False
    async with _lock:
        existing = await _read_unlocked()
        kept: list[dict[str, Any]] = []
        deleted = False
        for job in existing:
            jid = str(job.get("id") or "")
            mid = str(job.get("message_id") or "")
            if jid == needle or mid == needle:
                deleted = True
                continue
            kept.append(job)
        if deleted:
            await _write_unlocked(kept)
        return deleted


async def delete_jobs_bulk(job_ids: Iterable[str]) -> dict[str, Any]:
    """
    Remove multiple jobs by id or message_id in one locked write.
    Returns {deleted_count, deleted_ids, not_found}.
    """
    needles = {str(j).strip() for j in (job_ids or []) if str(j).strip()}
    if not needles:
        return {"deleted_count": 0, "deleted_ids": [], "not_found": []}

    async with _lock:
        existing = await _read_unlocked()
        kept: list[dict[str, Any]] = []
        deleted_ids: list[str] = []
        matched: set[str] = set()
        for job in existing:
            jid = str(job.get("id") or "")
            mid = str(job.get("message_id") or "")
            hit = None
            if jid and jid in needles:
                hit = jid
            elif mid and mid in needles:
                hit = mid
            if hit is not None:
                deleted_ids.append(jid or mid)
                matched.add(hit)
                if jid:
                    matched.add(jid)
                if mid:
                    matched.add(mid)
                continue
            kept.append(job)
        if deleted_ids:
            await _write_unlocked(kept)
        not_found = sorted(needles - matched)
        return {
            "deleted_count": len(deleted_ids),
            "deleted_ids": deleted_ids,
            "not_found": not_found,
        }


async def clear_all_jobs() -> int:
    """Wipe jobs.json to an empty list. Returns how many jobs were removed."""
    async with _lock:
        existing = await _read_unlocked()
        count = len(existing)
        await _write_unlocked([])
        return count


async def get_distinct_categories() -> list[str]:
    """Return unique non-empty categories currently stored in jobs.json."""
    jobs = await read_jobs()
    return sorted(
        normalize_label_list(
            str(j.get("category"))
            for j in jobs
            if j.get("category")
        ),
        key=str.casefold,
    )


def _parse_job_date(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _parse_ymd(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def resolve_date_bounds(
    preset: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    *,
    now: datetime | None = None,
) -> tuple[datetime | None, datetime | None]:
    """
    Resolve inclusive date bounds (UTC) from preset and/or custom YYYY-MM-DD.
    Custom start/end override preset when provided (and preset is custom/empty).
    """
    now = now or datetime.now(timezone.utc)
    today = now.date()
    key = (preset or "all_time").strip().lower()

    start_d = _parse_ymd(start_date)
    end_d = _parse_ymd(end_date)

    if key == "custom" or (start_d or end_d):
        start_dt = (
            datetime.combine(start_d, time.min, tzinfo=timezone.utc) if start_d else None
        )
        end_dt = (
            datetime.combine(end_d, time.max, tzinfo=timezone.utc) if end_d else None
        )
        return start_dt, end_dt

    if key == "today":
        start_dt = datetime.combine(today, time.min, tzinfo=timezone.utc)
        end_dt = datetime.combine(today, time.max, tzinfo=timezone.utc)
        return start_dt, end_dt

    if key == "this_week":
        # Monday-start ISO week
        week_start = today - timedelta(days=today.weekday())
        start_dt = datetime.combine(week_start, time.min, tzinfo=timezone.utc)
        end_dt = datetime.combine(today, time.max, tzinfo=timezone.utc)
        return start_dt, end_dt

    if key == "this_month":
        month_start = today.replace(day=1)
        start_dt = datetime.combine(month_start, time.min, tzinfo=timezone.utc)
        end_dt = datetime.combine(today, time.max, tzinfo=timezone.utc)
        return start_dt, end_dt

    # all_time
    return None, None


def _job_in_range(
    job: dict[str, Any],
    start_dt: datetime | None,
    end_dt: datetime | None,
) -> bool:
    if start_dt is None and end_dt is None:
        return True
    job_dt = _parse_job_date(job.get("date"))
    if job_dt is None:
        return False
    if start_dt and job_dt < start_dt:
        return False
    if end_dt and job_dt > end_dt:
        return False
    return True


async def get_jobs_sorted(
    category: str | None = None,
    sort_by: str = "date_desc",
    preset: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    """Return jobs with optional category, date, and sort filters."""
    jobs = await read_jobs()
    if category:
        needle = category.strip()
        jobs = [
            j
            for j in jobs
            if str(j.get("category") or "").strip() == needle
        ]

    start_dt, end_dt = resolve_date_bounds(preset, start_date, end_date)
    if start_dt or end_dt:
        jobs = [j for j in jobs if _job_in_range(j, start_dt, end_dt)]

    key = (sort_by or "date_desc").strip().lower()

    if key == "date_asc":
        jobs = sorted(jobs, key=lambda j: str(j.get("date") or ""), reverse=False)
    elif key == "category_asc":
        jobs = sorted(
            jobs,
            key=lambda j: (
                str(j.get("category") or "").lower(),
                str(j.get("date") or ""),
            ),
        )
    elif key == "title_asc":
        jobs = sorted(
            jobs,
            key=lambda j: (
                str(j.get("title") or "").lower(),
                str(j.get("date") or ""),
            ),
        )
    else:  # date_desc default
        jobs = sorted(jobs, key=lambda j: str(j.get("date") or ""), reverse=True)

    return jobs


async def save_debug_sample(data: dict[str, Any]) -> dict[str, Any]:
    """Append a discovery debug record to debug_samples.json (newest last)."""
    path = settings.debug_samples_file
    _ensure_file(path)
    record = dict(data)
    record.setdefault("timestamp", datetime.now(timezone.utc).isoformat())

    async with _debug_lock:
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            raw = await f.read()
        try:
            existing = json.loads(raw or "[]")
            if not isinstance(existing, list):
                existing = []
        except json.JSONDecodeError:
            existing = []
        existing.append(record)
        # Cap history to keep file manageable (per-chunk + summary rows)
        if len(existing) > 120:
            existing = existing[-120:]
        payload = json.dumps(existing, ensure_ascii=False, indent=2)
        tmp = path.with_suffix(".json.tmp")
        async with aiofiles.open(tmp, "w", encoding="utf-8") as f:
            await f.write(payload)
        tmp.replace(path)
    return record


async def read_debug_samples(limit: int = 20) -> list[dict[str, Any]]:
    path = settings.debug_samples_file
    _ensure_file(path)
    async with _debug_lock:
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            raw = await f.read()
    try:
        data = json.loads(raw or "[]")
        if not isinstance(data, list):
            return []
    except json.JSONDecodeError:
        return []
    if limit <= 0:
        return data
    return data[-limit:]


_CHANNEL_LANGUAGES = ("English", "Persian", "Arabic")


def _normalize_channel_language(raw: str | None) -> str:
    text = str(raw or "English").strip()
    aliases = {
        "en": "English",
        "english": "English",
        "fa": "Persian",
        "farsi": "Persian",
        "persian": "Persian",
        "ar": "Arabic",
        "arabic": "Arabic",
    }
    return aliases.get(text.casefold(), text if text in _CHANNEL_LANGUAGES else "English")


def normalize_channel_handle(raw: str | None) -> str:
    """Trim a destination handle and ensure a leading `@` for usernames."""
    text = str(raw or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered.startswith("https://t.me/") or lowered.startswith("http://t.me/"):
        text = text.rstrip("/").split("/")[-1]
    if text.lstrip("-").isdigit():
        return text
    text = text.lstrip("@").strip()
    if not text:
        return ""
    return f"@{text}"


def _apply_channel_defaults(channel: dict[str, Any], *, fallback_id: str = "c1") -> dict[str, Any]:
    handle = normalize_channel_handle(channel.get("handle") or channel.get("username"))
    name = str(channel.get("name") or channel.get("title") or "").strip()
    if not name and handle:
        name = handle.lstrip("@")
    channel_id = str(channel.get("id") or "").strip() or fallback_id
    return {
        "id": channel_id,
        "name": name or handle or channel_id,
        "handle": handle,
        "is_default": bool(channel.get("is_default", False)),
        "default_language": _normalize_channel_language(channel.get("default_language")),
    }


def _next_channel_id(existing: list[dict[str, Any]]) -> str:
    highest = 0
    for channel in existing:
        match = _CHANNEL_ID_RE.match(str(channel.get("id") or ""))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"c{highest + 1}"


def _seed_channels_from_env() -> list[dict[str, Any]]:
    handle = normalize_channel_handle(settings.telegram_target_channel)
    if not handle:
        return []
    return [
        {
            "id": "c1",
            "name": handle.lstrip("@"),
            "handle": handle,
            "is_default": True,
            "default_language": "English",
        }
    ]


def _ensure_channels_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("[]", encoding="utf-8")


async def _read_channels_unlocked() -> list[dict[str, Any]]:
    path = settings.channels_file
    _ensure_channels_file(path)
    async with aiofiles.open(path, "r", encoding="utf-8") as f:
        raw = await f.read()
    try:
        data = json.loads(raw or "[]")
        if not isinstance(data, list):
            data = []
    except json.JSONDecodeError:
        data = []

    channels: list[dict[str, Any]] = []
    seen_handles: set[str] = set()
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            continue
        record = _apply_channel_defaults(item, fallback_id=f"c{index}")
        if not record["handle"]:
            continue
        key = record["handle"].casefold()
        if key in seen_handles:
            continue
        seen_handles.add(key)
        channels.append(record)

    if not channels:
        seeded = _seed_channels_from_env()
        if seeded:
            await _write_channels_unlocked(seeded)
            return seeded
    return channels


async def _write_channels_unlocked(channels: list[dict[str, Any]]) -> None:
    path = settings.channels_file
    _ensure_channels_file(path)
    payload = json.dumps(channels, ensure_ascii=False, indent=2)
    tmp = path.with_suffix(".json.tmp")
    async with aiofiles.open(tmp, "w", encoding="utf-8") as f:
        await f.write(payload)
    tmp.replace(path)


async def get_admin_channels() -> list[dict[str, Any]]:
    """Return saved destination channels from channels.json."""
    async with _channels_lock:
        return await _read_channels_unlocked()


async def get_default_admin_handles() -> list[str]:
    """Handles marked is_default=True, in stored order."""
    return [
        channel["handle"]
        for channel in await get_admin_channels()
        if channel.get("is_default") and channel.get("handle")
    ]


async def add_admin_channel(
    name: str,
    handle: str,
    default_language: str = "English",
) -> dict[str, Any]:
    """
    Append a destination channel. The first saved channel becomes default.
    Raises ValueError when name/handle is missing or the handle already exists.
    """
    cleaned_handle = normalize_channel_handle(handle)
    cleaned_name = str(name or "").strip() or cleaned_handle.lstrip("@")
    if not cleaned_handle:
        raise ValueError("Channel handle is required (for example @huntjobarmenia)")
    if not cleaned_name:
        raise ValueError("Channel name is required")

    async with _channels_lock:
        existing = await _read_channels_unlocked()
        if any(str(item.get("handle") or "").casefold() == cleaned_handle.casefold() for item in existing):
            raise ValueError(f"Channel {cleaned_handle} is already saved")
        record = {
            "id": _next_channel_id(existing),
            "name": cleaned_name,
            "handle": cleaned_handle,
            "is_default": len(existing) == 0 or not any(item.get("is_default") for item in existing),
            "default_language": _normalize_channel_language(default_language),
        }
        existing.append(record)
        await _write_channels_unlocked(existing)
        return record


async def delete_admin_channel(channel_id: str) -> bool:
    """Remove a saved destination channel by id. Returns True if deleted."""
    needle = str(channel_id or "").strip()
    if not needle:
        return False
    async with _channels_lock:
        existing = await _read_channels_unlocked()
        kept = [item for item in existing if str(item.get("id") or "") != needle]
        if len(kept) == len(existing):
            return False
        await _write_channels_unlocked(kept)
        return True


async def toggle_default_channel(channel_id: str) -> dict[str, Any] | None:
    """Flip is_default on the matching channel. Returns the updated record or None."""
    needle = str(channel_id or "").strip()
    if not needle:
        return None
    async with _channels_lock:
        existing = await _read_channels_unlocked()
        updated: dict[str, Any] | None = None
        for item in existing:
            if str(item.get("id") or "") == needle:
                item["is_default"] = not bool(item.get("is_default"))
                updated = item
                break
        if updated is None:
            return None
        await _write_channels_unlocked(existing)
        return dict(updated)


async def set_channel_language(channel_id: str, default_language: str) -> dict[str, Any] | None:
    """Set a saved channel's default publish language. Returns the updated record or None."""
    needle = str(channel_id or "").strip()
    if not needle:
        return None
    language = _normalize_channel_language(default_language)
    async with _channels_lock:
        existing = await _read_channels_unlocked()
        updated: dict[str, Any] | None = None
        for item in existing:
            if str(item.get("id") or "") == needle:
                item["default_language"] = language
                updated = item
                break
        if updated is None:
            return None
        await _write_channels_unlocked(existing)
        return dict(updated)


def _apply_scraper_channel_defaults(
    channel: dict[str, Any],
    *,
    fallback_id: str = "s1",
) -> dict[str, Any]:
    handle = normalize_channel_handle(channel.get("handle") or channel.get("username"))
    name = str(channel.get("name") or channel.get("title") or "").strip()
    if not name and handle:
        name = handle.lstrip("@")
    channel_id = str(channel.get("id") or "").strip() or fallback_id
    return {
        "id": channel_id,
        "name": name or handle or channel_id,
        "handle": handle,
    }


def _next_scraper_channel_id(existing: list[dict[str, Any]]) -> str:
    highest = 0
    for channel in existing:
        match = _SCRAPER_CHANNEL_ID_RE.match(str(channel.get("id") or ""))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"s{highest + 1}"


def _ensure_scraper_channels_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("[]", encoding="utf-8")


async def _read_scraper_channels_unlocked() -> list[dict[str, Any]]:
    path = settings.scraper_channels_file
    _ensure_scraper_channels_file(path)
    async with aiofiles.open(path, "r", encoding="utf-8") as f:
        raw = await f.read()
    try:
        data = json.loads(raw or "[]")
        if not isinstance(data, list):
            data = []
    except json.JSONDecodeError:
        data = []

    channels: list[dict[str, Any]] = []
    seen_handles: set[str] = set()
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            continue
        record = _apply_scraper_channel_defaults(item, fallback_id=f"s{index}")
        if not record["handle"]:
            continue
        key = record["handle"].casefold()
        if key in seen_handles:
            continue
        seen_handles.add(key)
        channels.append(record)
    return channels


async def _write_scraper_channels_unlocked(channels: list[dict[str, Any]]) -> None:
    path = settings.scraper_channels_file
    _ensure_scraper_channels_file(path)
    payload = json.dumps(channels, ensure_ascii=False, indent=2)
    tmp = path.with_suffix(".json.tmp")
    async with aiofiles.open(tmp, "w", encoding="utf-8") as f:
        await f.write(payload)
    tmp.replace(path)


async def get_scraper_channels() -> list[dict[str, Any]]:
    """Return saved source scrape channels from scraper_channels.json."""
    async with _scraper_lock:
        return await _read_scraper_channels_unlocked()


async def add_scraper_channel(handle: str, name: str | None = None) -> dict[str, Any]:
    """
    Append a source scrape channel. Handle is normalized with a leading `@`.
    Raises ValueError when the handle is missing or already saved.
    """
    cleaned_handle = normalize_channel_handle(handle)
    cleaned_name = str(name or "").strip() or cleaned_handle.lstrip("@")
    if not cleaned_handle:
        raise ValueError("Channel handle is required (for example @job_am)")
    if not cleaned_name:
        raise ValueError("Channel name is required")

    async with _scraper_lock:
        existing = await _read_scraper_channels_unlocked()
        if any(
            str(item.get("handle") or "").casefold() == cleaned_handle.casefold()
            for item in existing
        ):
            raise ValueError(f"Source channel {cleaned_handle} is already saved")
        record = {
            "id": _next_scraper_channel_id(existing),
            "name": cleaned_name,
            "handle": cleaned_handle,
        }
        existing.append(record)
        await _write_scraper_channels_unlocked(existing)
        return record


async def delete_scraper_channel(channel_id: str) -> bool:
    """Remove a saved source scrape channel by id. Returns True if deleted."""
    needle = str(channel_id or "").strip()
    if not needle:
        return False
    async with _scraper_lock:
        existing = await _read_scraper_channels_unlocked()
        kept = [item for item in existing if str(item.get("id") or "") != needle]
        if len(kept) == len(existing):
            return False
        await _write_scraper_channels_unlocked(kept)
        return True
