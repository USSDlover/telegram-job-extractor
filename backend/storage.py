"""Async JSON persistence for extracted jobs with lock-safe I/O."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import aiofiles

from config import settings

_lock = asyncio.Lock()


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
    cats = {
        str(j.get("category")).strip()
        for j in jobs
        if j.get("category") and str(j.get("category")).strip()
    }
    return sorted(cats)


async def get_jobs_sorted(
    category: str | None = None,
    sort_by: str = "date_desc",
) -> list[dict[str, Any]]:
    """Return jobs with optional category filter and sort order."""
    jobs = await read_jobs()
    if category:
        needle = category.strip()
        jobs = [
            j
            for j in jobs
            if str(j.get("category") or "").strip() == needle
        ]

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
