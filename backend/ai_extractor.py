"""Ollama / Gemma 2 structured extraction using Pydantic schemas."""

from __future__ import annotations

import json
import logging
import re
from typing import List, Optional

import httpx
from pydantic import BaseModel, Field, ValidationError

from activity_log import broadcast_log
from config import settings

logger = logging.getLogger(__name__)


class ExtractedJob(BaseModel):
    is_job_posting: bool = False
    title: str = ""
    category: str = ""
    company: Optional[str] = None
    apply_links: List[str] = Field(default_factory=list)
    translated_summary: str = ""


class CategoryDiscoveryResult(BaseModel):
    discovered_categories: List[str] = Field(default_factory=list)
    suggested_titles: List[str] = Field(default_factory=list)


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        return fence.group(1).strip()
    # Fallback: first {...} block
    brace = re.search(r"\{[\s\S]*\}", text)
    if brace:
        return brace.group(0)
    return text


async def _ollama_chat_json(system: str, user: str, schema: dict) -> dict:
    """Call Ollama chat API and parse a JSON object from the reply."""
    url = f"{settings.ollama_host}/api/chat"
    schema_text = json.dumps(schema, indent=2)
    messages = [
        {
            "role": "system",
            "content": (
                f"{system}\n\n"
                "Respond with ONLY valid JSON matching this JSON Schema. "
                "No markdown, no commentary.\n\n"
                f"{schema_text}"
            ),
        },
        {"role": "user", "content": user},
    ]
    payload = {
        "model": settings.ollama_model,
        "messages": messages,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1},
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
    content = (data.get("message") or {}).get("content") or ""
    cleaned = _strip_json_fence(content)
    return json.loads(cleaned)


async def discover_categories_from_samples(
    sample_texts: List[str],
) -> CategoryDiscoveryResult:
    """Analyze sampled channel posts and return categories + suggested titles."""
    if not sample_texts:
        await broadcast_log(
            "CALLING_OLLAMA",
            "No sample posts available for category discovery.",
            {"sample_count": 0},
        )
        return CategoryDiscoveryResult()

    numbered = "\n\n---\n\n".join(
        f"[Sample {i + 1}]\n{t[:2000]}" for i, t in enumerate(sample_texts)
    )
    system = (
        "You analyze Telegram channel posts that may contain job listings. "
        "Identify distinct job categories and normalized English job titles "
        "present in the samples (e.g. Frontend Developer, Full Stack, "
        "Game Presenter, Waiter)."
    )
    user = f"Sample posts:\n\n{numbered}"
    schema = CategoryDiscoveryResult.model_json_schema()

    await broadcast_log(
        "CALLING_OLLAMA",
        "Sending sample posts to Gemma 2 for category discovery...",
        {"sample_count": len(sample_texts), "model": settings.ollama_model},
    )
    try:
        raw = await _ollama_chat_json(system, user, schema)
        result = CategoryDiscoveryResult.model_validate(raw)
        # Deduplicate while preserving order
        result.discovered_categories = list(
            dict.fromkeys(c.strip() for c in result.discovered_categories if c.strip())
        )
        result.suggested_titles = list(
            dict.fromkeys(t.strip() for t in result.suggested_titles if t.strip())
        )
        return result
    except (httpx.HTTPError, json.JSONDecodeError, ValidationError, KeyError) as exc:
        logger.exception("Category discovery failed: %s", exc)
        await broadcast_log(
            "ERROR",
            f"Ollama category discovery failed: {exc}",
            {"model": settings.ollama_model},
        )
        return CategoryDiscoveryResult()


async def extract_job_data(message_text: str) -> Optional[ExtractedJob]:
    """
    Extract structured job data from a single message.
    Returns ExtractedJob if it is a job posting with at least one apply link;
    otherwise returns None.
    """
    if not message_text or not message_text.strip():
        return None

    system = (
        "You extract structured job posting data from Telegram messages. "
        "Normalize titles to concise English (e.g. Frontend Developer, Full Stack, "
        "Game Presenter, Waiter). Translate summaries to clear English. "
        "Collect external application URLs, forms, or contact links into apply_links. "
        "Set is_job_posting=false if the message is not a job ad."
    )
    user = f"Message:\n\n{message_text[:4000]}"
    schema = ExtractedJob.model_json_schema()

    try:
        raw = await _ollama_chat_json(system, user, schema)
        job = ExtractedJob.model_validate(raw)
    except (httpx.HTTPError, json.JSONDecodeError, ValidationError, KeyError) as exc:
        logger.warning("Job extraction failed: %s", exc)
        await broadcast_log(
            "ERROR",
            f"Ollama job extraction failed: {exc}",
            {"model": settings.ollama_model},
        )
        return None

    if not job.is_job_posting:
        return None
    if not job.apply_links:
        return None

    # Normalize empty optional fields
    job.title = (job.title or "").strip() or "Untitled Role"
    job.category = (job.category or "").strip() or "Uncategorized"
    job.translated_summary = (job.translated_summary or "").strip()
    job.apply_links = [u.strip() for u in job.apply_links if u and u.strip()]
    if not job.apply_links:
        return None
    return job
