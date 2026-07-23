"""Async JSON persistence for extracted jobs and discovery debug samples."""

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
_PUNCT_TRIM = re.compile(r"^[\s\-–—|:;,.]+|[\s\-–—|:;,.]+$")


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


async def _read_unlocked() -> list[dict[str, Any]]:
    path = settings.jobs_file
    _ensure_file(path)
    async with aiofiles.open(path, "r", encoding="utf-8") as f:
        raw = await f.read()
    try:
        data = json.loads(raw or "[]")
        return data if isinstance(data, list) else []
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
        for job in new_jobs:
            job_id = job.get("id")
            if not job_id:
                continue
            if job_id not in by_id:
                inserted += 1
            by_id[job_id] = job
        await _write_unlocked(list(by_id.values()))
        return inserted


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
        # Cap history to keep file manageable
        if len(existing) > 50:
            existing = existing[-50:]
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
