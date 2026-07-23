"""Ollama / Gemma 2 structured extraction using Pydantic schemas."""

from __future__ import annotations

import json
import logging
import re
from typing import List, Optional, Sequence

import httpx
from pydantic import BaseModel, Field, ValidationError

from activity_log import broadcast_log
from config import settings
from link_preview import enrich_message_with_link_metadata, should_enrich_message
from storage import normalize_label_list, save_debug_sample

logger = logging.getLogger(__name__)


class ExtractedJob(BaseModel):
    is_job_posting: bool = False
    title: str = ""
    category: str = ""
    company: Optional[str] = None
    apply_links: List[str] = Field(default_factory=list)
    translated_summary: str = ""
    original_language: Optional[str] = None


class CategoryDiscoveryResult(BaseModel):
    discovered_categories: List[str] = Field(default_factory=list)
    suggested_titles: List[str] = Field(default_factory=list)


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        return fence.group(1).strip()
    brace = re.search(r"\{[\s\S]*\}", text)
    if brace:
        return brace.group(0)
    return text


def _build_messages(system: str, user: str, schema: dict) -> tuple[list[dict], str]:
    schema_text = json.dumps(schema, indent=2)
    system_content = (
        f"{system}\n\n"
        "Respond with ONLY valid JSON matching this JSON Schema. "
        "No markdown, no commentary.\n\n"
        f"{schema_text}"
    )
    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user},
    ]
    prompt_sent = f"[SYSTEM]\n{system_content}\n\n[USER]\n{user}"
    return messages, prompt_sent


async def _ollama_chat_json(
    system: str,
    user: str,
    schema: dict,
) -> tuple[dict, str, str]:
    """
    Call Ollama chat API.
    Returns (parsed_json, raw_content, prompt_sent_to_ollama).
    """
    url = f"{settings.ollama_host}/api/chat"
    messages, prompt_sent = _build_messages(system, user, schema)
    payload = {
        "model": settings.ollama_model,
        "messages": messages,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1},
    }
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
    content = (data.get("message") or {}).get("content") or ""
    cleaned = _strip_json_fence(content)
    return json.loads(cleaned), content, prompt_sent


def _format_sample_block(texts: Sequence[str], start_index: int = 1) -> str:
    parts = []
    for offset, text in enumerate(texts):
        clipped = (text or "")[:1500]
        parts.append(f"[Sample {start_index + offset}]\n{clipped}")
    return "\n\n---\n\n".join(parts)


def _chunk_samples(sample_texts: List[str]) -> List[List[str]]:
    """Split samples into chunks that fit discovery prompt budget."""
    max_posts = max(1, settings.discovery_chunk_posts)
    max_chars = max(2000, settings.discovery_chunk_chars)
    chunks: List[List[str]] = []
    current: List[str] = []
    current_chars = 0

    for text in sample_texts:
        piece = (text or "")[:1500]
        piece_len = len(piece) + 32  # separators / labels overhead
        would_overflow = (
            current
            and (
                len(current) >= max_posts
                or current_chars + piece_len > max_chars
            )
        )
        if would_overflow:
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(text)
        current_chars += piece_len

    if current:
        chunks.append(current)
    return chunks or [[]]


def _merge_discovery_results(
    parts: List[CategoryDiscoveryResult],
) -> CategoryDiscoveryResult:
    categories: list[str] = []
    titles: list[str] = []
    for part in parts:
        categories.extend(part.discovered_categories)
        titles.extend(part.suggested_titles)
    return CategoryDiscoveryResult(
        discovered_categories=normalize_label_list(categories),
        suggested_titles=normalize_label_list(titles),
    )


async def discover_categories_from_samples(
    sample_texts: List[str],
    channels: Optional[Sequence[str]] = None,
) -> CategoryDiscoveryResult:
    """
    Analyze sampled channel posts and return categories + suggested titles.
    Large sample sets are processed in chunks; debug payloads are persisted.
    Short/link-only samples are enriched with fetched page metadata first.
    """
    channel_list = list(channels or [])
    if not sample_texts:
        await broadcast_log(
            "CALLING_OLLAMA",
            "No sample posts available for category discovery.",
            {"sample_count": 0},
        )
        await save_debug_sample(
            {
                "channels_sampled": channel_list,
                "total_posts_collected": 0,
                "sample_texts_preview": [],
                "prompt_sent_to_ollama": "",
                "raw_ollama_response": "",
                "chunk_count": 0,
                "chunks": [],
                "parsed_categories_count": 0,
                "parsed_titles_count": 0,
                "discovered_categories": [],
                "suggested_titles": [],
                "enriched_metadata": [],
                "note": "No sample posts collected",
            }
        )
        return CategoryDiscoveryResult()

    # Enrich short/link-only samples (cap fetches to keep discovery responsive)
    enriched_samples: List[str] = []
    enriched_metadata: list[dict] = []
    enrich_budget = 20
    for text in sample_texts:
        if enrich_budget > 0 and should_enrich_message(text):
            enriched, meta = await enrich_message_with_link_metadata(text, log=True)
            enriched_samples.append(enriched)
            if meta:
                enriched_metadata.append(
                    {
                        "original_preview": (text or "")[:240],
                        "metadata": meta,
                    }
                )
            enrich_budget -= 1
        else:
            enriched_samples.append(text)

    system = (
        "You are an expert multilingual job classification and translation assistant. "
        "Sample posts may be written in Armenian, Russian, English, or a mix. "
        "Some posts include appended [Fetched Web Page Metadata] from linked pages — "
        "use both the Telegram text AND that metadata. "
        "Identify distinct job categories and normalized English job titles "
        "(e.g. Frontend Developer, Full Stack, Game Presenter, Waiter). "
        "ALWAYS output category and title strings in fluent English only — "
        "NEVER return Armenian script or Russian Cyrillic in discovered_categories or suggested_titles. "
        "Return as many relevant categories and titles as you can find; "
        "use empty lists only if none are present."
    )
    schema = CategoryDiscoveryResult.model_json_schema()
    chunks = _chunk_samples(enriched_samples)

    await broadcast_log(
        "CALLING_OLLAMA",
        f"Sending {len(enriched_samples)} sample posts to Gemma 2 "
        f"in {len(chunks)} chunk(s) for category discovery...",
        {
            "sample_count": len(enriched_samples),
            "chunk_count": len(chunks),
            "enriched_link_posts": len(enriched_metadata),
            "model": settings.ollama_model,
        },
    )

    results: List[CategoryDiscoveryResult] = []
    chunk_debug: list[dict] = []
    prompt_parts: list[str] = []
    raw_parts: list[str] = []
    sample_index = 1

    for idx, chunk in enumerate(chunks, start=1):
        user = (
            f"Sample posts (chunk {idx}/{len(chunks)}):\n\n"
            f"{_format_sample_block(chunk, start_index=sample_index)}"
        )
        sample_index += len(chunk)
        try:
            raw, raw_content, prompt_sent = await _ollama_chat_json(system, user, schema)
            part = CategoryDiscoveryResult.model_validate(raw)
            part.discovered_categories = normalize_label_list(part.discovered_categories)
            part.suggested_titles = normalize_label_list(part.suggested_titles)
            results.append(part)
            prompt_parts.append(prompt_sent)
            raw_parts.append(raw_content)
            chunk_debug.append(
                {
                    "chunk_index": idx,
                    "posts_in_chunk": len(chunk),
                    "prompt_chars": len(prompt_sent),
                    "prompt_sent_to_ollama": prompt_sent,
                    "raw_ollama_response": raw_content,
                    "parsed_categories": part.discovered_categories,
                    "parsed_titles": part.suggested_titles,
                }
            )
            await broadcast_log(
                "CALLING_OLLAMA",
                f"Chunk {idx}/{len(chunks)} returned "
                f"{len(part.discovered_categories)} categories, "
                f"{len(part.suggested_titles)} titles.",
                {"chunk": idx, "total_chunks": len(chunks)},
            )
        except (httpx.HTTPError, json.JSONDecodeError, ValidationError, KeyError) as exc:
            logger.exception("Category discovery chunk %s failed: %s", idx, exc)
            await broadcast_log(
                "ERROR",
                f"Ollama category discovery chunk {idx}/{len(chunks)} failed: {exc}",
                {"model": settings.ollama_model, "chunk": idx},
            )
            chunk_debug.append(
                {
                    "chunk_index": idx,
                    "posts_in_chunk": len(chunk),
                    "error": str(exc),
                    "prompt_sent_to_ollama": _build_messages(system, user, schema)[1],
                    "raw_ollama_response": "",
                    "parsed_categories": [],
                    "parsed_titles": [],
                }
            )
            prompt_parts.append(chunk_debug[-1]["prompt_sent_to_ollama"])
            raw_parts.append("")

    merged = _merge_discovery_results(results)
    preview = [
        (t[:400] + ("…" if len(t) > 400 else ""))
        for t in enriched_samples[:40]
    ]
    await save_debug_sample(
        {
            "channels_sampled": channel_list,
            "total_posts_collected": len(sample_texts),
            "sample_texts_preview": preview,
            "prompt_sent_to_ollama": "\n\n===== CHUNK SEPARATOR =====\n\n".join(prompt_parts),
            "raw_ollama_response": "\n\n===== CHUNK SEPARATOR =====\n\n".join(raw_parts),
            "chunk_count": len(chunks),
            "chunks": chunk_debug,
            "parsed_categories_count": len(merged.discovered_categories),
            "parsed_titles_count": len(merged.suggested_titles),
            "discovered_categories": merged.discovered_categories,
            "suggested_titles": merged.suggested_titles,
            "enriched_metadata": enriched_metadata,
        }
    )
    return merged


async def extract_job_data(message_text: str) -> Optional[ExtractedJob]:
    """
    Extract structured job data from a single message (optionally already enriched
    with fetched web page metadata).
    Returns ExtractedJob if it is a job posting with at least one apply link;
    otherwise returns None.
    """
    if not message_text or not message_text.strip():
        return None

    system = (
        "You are an expert multilingual job classification and translation assistant. "
        "The input text may be written in Armenian, Russian, English, or a mix of these languages. "
        "Some posts include appended [Fetched Web Page Metadata] — use both Telegram text and metadata. "
        "Your instructions: "
        "1. Detect the original language (e.g., Armenian, Russian, English). "
        "2. Translate the job title and full job summary completely into fluent, professional English. "
        "3. Ensure normalized target categories and job titles are in English "
        "(e.g., Frontend Developer, Full Stack, Game Presenter, Waiter). "
        "4. NEVER return Armenian (Armenian script/Hayeren) or Russian (Cyrillic script) "
        "in the title, category, or translated_summary output fields. "
        "5. Set original_language to the detected source language name when not English "
        "(e.g., Armenian, Russian). "
        "Prefer application URLs from the Telegram message; include page URLs when they are apply links. "
        "Set is_job_posting=false if the message is not a job ad."
    )
    user = f"Message:\n\n{message_text[:6000]}"
    schema = ExtractedJob.model_json_schema()

    try:
        raw, _, _ = await _ollama_chat_json(system, user, schema)
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

    job.title = (job.title or "").strip() or "Untitled Role"
    job.category = (job.category or "").strip() or "Uncategorized"
    job.translated_summary = (job.translated_summary or "").strip()
    job.apply_links = [u.strip() for u in job.apply_links if u and u.strip()]
    if not job.apply_links:
        return None
    return job
