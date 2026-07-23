"""Ollama / Gemma 2 structured extraction using Pydantic schemas."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, List, Optional, Sequence, Union

import httpx
from pydantic import BaseModel, Field, ValidationError

from activity_log import broadcast_log
from config import settings
from link_preview import enrich_message_with_link_metadata, extract_urls, should_enrich_message
from storage import normalize_label_list, save_debug_sample

logger = logging.getLogger(__name__)

SampleInput = Union[str, dict[str, Any]]


class ExtractedJob(BaseModel):
    is_job_posting: bool = False
    title: str = ""
    category: str = ""
    company: Optional[str] = None
    apply_links: List[str] = Field(default_factory=list)
    translated_summary: str = ""
    original_language: Optional[str] = None


class CategoryDiscoveryResult(BaseModel):
    discovered_categories: List[str] = Field(
        default_factory=list,
        description=(
            "Unique, normalized job categories translated into English "
            "(e.g. Procurement / Purchasing, Hospitality / Food Service, Software Engineering)"
        ),
    )


DISCOVERY_SYSTEM_PROMPT = (
    "You are a job market categorization assistant for Armenian and international job listings.\n"
    "Analyze the sanitized post titles and English URL slugs below.\n\n"
    "Instructions:\n"
    "1. Translate Armenian job titles or URL slugs to English:\n"
    '   - "Պետական գնումների մասնագետ" or "petakan-gnumneri-masnaget" '
    "-> Public Procurement Specialist -> Category: Procurement & Purchasing\n"
    '   - "Վաճառքի Մենեջեր" or "vacharqi-menejer" '
    "-> Sales Manager -> Category: Sales & Business Development\n"
    '   - "Խմբային տուրերի գիդ" or "xmbayin-tureri-gid" '
    "-> Group Tour Guide -> Category: Tourism & Hospitality\n"
    '   - "HR Assistant" or "hr-assistant" -> HR Assistant -> Category: Human Resources\n'
    "2. Extract high-level, normalized English job categories "
    "(e.g., Sales, Human Resources, Procurement, Tourism, Software Development).\n"
    "3. If an input item contains an obvious job title or URL slug, NEVER return an empty list. "
    "Always infer a broad professional category.\n"
    "4. All category names MUST be clean professional English — never Armenian or Russian script."
)

EXTRACTION_SYSTEM_PROMPT = (
    "You are a multilingual job extraction AI. Telegram posts are frequently short and "
    "written in Armenian script (e.g., \"Պետական գնումների մասնագետ\") or Russian Cyrillic "
    "(e.g., \"Специалист по госзакупкам\"), often with only a title plus a link / "
    "page metadata.\n\n"
    "CRITICAL INSTRUCTIONS:\n"
    "1. TRANSLATE FIRST: Translate all non-English text into fluent English before filling fields.\n"
    '   - Example: "Պետական գնումների մասնագետ" -> title "Public Procurement Specialist", '
    'category "Procurement & Purchasing".\n'
    '   - Example: "Բարիստա" -> title "Barista", category "Hospitality & Food Service".\n'
    "2. SHORT TEXT IS VALID: A single job title (1-5 words) plus a link IS a valid job posting. "
    "Set is_job_posting=true even when salary/requirements are missing. Use page metadata / URL slugs when present.\n"
    "3. CATEGORY MAP: Map titles to broad English categories "
    "(Procurement, Software Engineering, Marketing, Finance, Sales, Customer Service, etc.).\n"
    "4. NO HY/RU OUTPUT: title, category, and translated_summary MUST be clean English — "
    "never Armenian or Russian script.\n"
    "5. Set original_language when the source is not English (e.g., Armenian, Russian).\n"
    "6. Prefer application URLs from the message / metadata; include page URLs when they are apply links.\n"
    "7. Set is_job_posting=false only if the message is clearly not a job ad."
)

# Trailing numeric job IDs common on job.am / staff.am: hr-assistant-77052
_SLUG_ID_TAIL_RE = re.compile(r"-\d{3,}$")
_NOISE_SECTION_RE = re.compile(
    r"\[(?:Telegram Message|Embedded Links|Detected External Links|"
    r"Fetched Web Page Metadata)\]:?\s*",
    re.IGNORECASE,
)
_NOISE_BLOCK_RE = re.compile(
    r"\[(?:Embedded Links|Detected External Links|Fetched Web Page Metadata)\]"
    r"[\s\S]*?(?=\[[A-Za-z]|\Z)",
    re.IGNORECASE,
)
_PAGE_TITLE_RE = re.compile(r"Page Title:\s*(.+)", re.IGNORECASE)
_PAGE_HEADING_RE = re.compile(r"Headings:\s*(.+)", re.IGNORECASE)

# Exact / prefix slug → (English title hint, category)
_SLUG_TRANSLATIONS: list[tuple[str, str, str]] = [
    ("petakan-gnumneri-masnaget", "Public Procurement Specialist", "Procurement & Purchasing"),
    ("petakan-gnumneri", "Public Procurement Specialist", "Procurement & Purchasing"),
    ("vacharqi-menejer", "Sales Manager", "Sales & Business Development"),
    ("vacharqi-meneger", "Sales Manager", "Sales & Business Development"),
    ("xmbayin-tureri-gid", "Group Tour Guide", "Tourism & Hospitality"),
    ("xmbayin-tureri", "Group Tour Guide", "Tourism & Hospitality"),
    ("dillerakan-hayteri", "Customer Support Specialist", "Customer Support & Operations"),
    ("hr-assistant", "HR Assistant", "Human Resources"),
    ("human-resources", "Human Resources", "Human Resources"),
    ("barista", "Barista", "Hospitality & Food Service"),
]

# Token → category (applied when no exact slug map hits)
_SLUG_TOKEN_CATEGORIES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(hr|human[-_]?resources?|rekruiter|recruiter)\b", re.I), "Human Resources"),
    (re.compile(r"\b(sales|vacharq|account[-_]?manager)\b", re.I), "Sales & Business Development"),
    (re.compile(r"\b(procurement|purchas|gnumner|goszakup)\b", re.I), "Procurement & Purchasing"),
    (re.compile(r"\b(tour|gid|ughekcord|tourism|hospitality|hotel|barista|waiter|oficiant)\b", re.I), "Tourism & Hospitality"),
    (re.compile(r"\b(diller|customer[-_]?support|call[-_]?center|operator)\b", re.I), "Customer Support & Operations"),
    (re.compile(r"\b(market|smm|content|brand)\b", re.I), "Marketing"),
    (re.compile(r"\b(financ|accountant|buchgalter|bookkeep)\b", re.I), "Finance & Accounting"),
    (re.compile(r"\b(developer|frontend|backend|fullstack|software|devops|qa|python|react|java)\b", re.I), "Software Development"),
    (re.compile(r"\b(design|ui|ux|graphic)\b", re.I), "Design"),
    (re.compile(r"\b(logist|driver|warehouse|sklad)\b", re.I), "Logistics & Operations"),
    (re.compile(r"\b(legal|lawyer|iravaban)\b", re.I), "Legal"),
    (re.compile(r"\b(teacher|tutor|educator|trainer)\b", re.I), "Education & Training"),
    (re.compile(r"\b(nurse|doctor|medical|pharma)\b", re.I), "Healthcare"),
]


def _title_case_slug(slug: str) -> str:
    parts = [p for p in re.split(r"[-_]+", slug or "") if p and not p.isdigit()]
    return " ".join(p[:1].upper() + p[1:] for p in parts)


def extract_slug_from_url(url: str) -> str:
    """
    Pull the job slug from a portal URL path.
    e.g. job.am/hy/job/hr-assistant-77052 -> hr-assistant
    """
    if not url:
        return ""
    try:
        from urllib.parse import urlparse

        path = (urlparse(url).path or "").strip("/")
    except Exception:
        path = url.split("?", 1)[0].split("#", 1)[0]
        path = path.split("//", 1)[-1]
        if "/" in path:
            path = path.split("/", 1)[-1]
        path = path.strip("/")
    if not path:
        return ""
    segments = [s for s in path.split("/") if s]
    # Skip locale / structural segments
    skip = {"hy", "en", "ru", "job", "jobs", "vacancy", "vacancies", "announcement"}
    candidates = [s for s in segments if s.lower() not in skip]
    if not candidates:
        return ""
    slug = candidates[-1]
    slug = _SLUG_ID_TAIL_RE.sub("", slug)
    slug = slug.strip("-_").lower()
    return slug


def extract_keywords_from_urls(urls: Sequence[str]) -> list[dict[str, str]]:
    """
    Deterministic keyword / category hints from job portal URL slugs.
    Returns list of {url, slug, title_hint, category}.
    """
    results: list[dict[str, str]] = []
    seen_slugs: set[str] = set()
    for raw in urls or []:
        url = (raw or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        slug = extract_slug_from_url(url)
        if not slug or slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        title_hint = _title_case_slug(slug)
        category = ""
        for key, mapped_title, mapped_cat in _SLUG_TRANSLATIONS:
            if slug == key or slug.startswith(key):
                title_hint = mapped_title
                category = mapped_cat
                break
        if not category:
            for pattern, mapped_cat in _SLUG_TOKEN_CATEGORIES:
                if pattern.search(slug.replace("-", " ")):
                    category = mapped_cat
                    break
        results.append(
            {
                "url": url,
                "slug": slug,
                "title_hint": title_hint,
                "category": category,
            }
        )
    return results


def _extract_caption(text: str) -> str:
    """Pull the human-visible post title, stripping enrichment noise blocks."""
    raw = text or ""
    m = re.search(
        r"\[Telegram Message\]:\s*(.*?)(?=\n\n\[|\Z)",
        raw,
        flags=re.IGNORECASE | re.DOTALL,
    )
    body = m.group(1) if m else raw
    body = _NOISE_BLOCK_RE.sub(" ", body)
    body = _NOISE_SECTION_RE.sub(" ", body)
    body = re.sub(r"https?://\S+", " ", body)
    body = re.sub(r"\s+", " ", body).strip(" -\n\t")
    return body[:220]


def _extract_page_title(text: str) -> str:
    for pattern in (_PAGE_TITLE_RE, _PAGE_HEADING_RE):
        m = pattern.search(text or "")
        if m:
            value = m.group(1).strip()
            value = value.split(" - ")[0].strip() if " - " in value else value
            return value[:200]
    return ""


def sanitize_discovery_post(sample: dict[str, str]) -> dict[str, Any]:
    """
    Convert a noisy enriched sample into a clean discovery line item.
    """
    text = sample.get("text") or ""
    channel = sample.get("channel") or "unknown"
    urls = extract_urls(text, include_telegram=False)
    slug_hits = extract_keywords_from_urls(urls)
    caption = _extract_caption(text)
    page_title = _extract_page_title(text)
    primary = slug_hits[0] if slug_hits else {}
    slug = primary.get("slug") or ""
    title = caption or page_title or primary.get("title_hint") or "(untitled)"
    category_hints = normalize_label_list(
        [h.get("category") for h in slug_hits if h.get("category")]
    )
    return {
        "channel": channel,
        "title": title,
        "slug": slug,
        "urls": urls,
        "slug_hits": slug_hits,
        "category_hints": category_hints,
        "page_title": page_title,
    }


def format_sanitized_posts(
    samples: Sequence[dict[str, str]],
    start_index: int = 1,
) -> tuple[str, list[dict[str, Any]]]:
    """Build clean Post N lines for Ollama + structured sanitized metadata."""
    sanitized: list[dict[str, Any]] = []
    lines: list[str] = []
    for offset, sample in enumerate(samples):
        item = sanitize_discovery_post(sample)
        sanitized.append(item)
        n = start_index + offset
        slug_bit = f" (URL Slug: {item['slug']})" if item.get("slug") else ""
        hint_bit = ""
        if item.get("category_hints"):
            hint_bit = f" [Hint: {item['category_hints'][0]}]"
        lines.append(f"Post {n}: {item['title']}{slug_bit}{hint_bit}")
    return "\n".join(lines), sanitized


def infer_categories_from_sanitized(sanitized: Sequence[dict[str, Any]]) -> list[str]:
    """Rule-based category inference from URL slugs + titles (fallback engine)."""
    categories: list[str] = []
    for item in sanitized:
        categories.extend(item.get("category_hints") or [])
        slug = (item.get("slug") or "").replace("-", " ")
        title = f"{item.get('title') or ''} {item.get('page_title') or ''} {slug}"
        for pattern, mapped_cat in _SLUG_TOKEN_CATEGORIES:
            if pattern.search(title):
                categories.append(mapped_cat)
        # Exact slug translations already covered via category_hints; keep title maps
        for key, _mapped_title, mapped_cat in _SLUG_TRANSLATIONS:
            if key in (item.get("slug") or "") or key.replace("-", " ") in title.lower():
                categories.append(mapped_cat)
    return normalize_label_list(categories)


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


def _normalize_samples(
    sample_texts: Sequence[SampleInput],
    channels: Optional[Sequence[str]] = None,
) -> list[dict[str, str]]:
    """
    Normalize discovery inputs to attributed posts:
    {"channel": "@handle", "text": "..."}.
    Accepts legacy bare strings (attributed to the sole channel when possible).
    """
    channel_list = [str(c).strip() for c in (channels or []) if str(c).strip()]
    sole = channel_list[0] if len(channel_list) == 1 else ""
    out: list[dict[str, str]] = []
    for item in sample_texts:
        if isinstance(item, dict):
            text = str(item.get("text") or "")
            ch = str(item.get("channel") or "").strip() or sole or "unknown"
            out.append({"channel": ch, "text": text})
        else:
            out.append({"channel": sole or "unknown", "text": str(item or "")})
    return out


def _channels_in_samples(samples: Sequence[dict[str, str]]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for sample in samples:
        ch = (sample.get("channel") or "").strip()
        if not ch or ch in seen:
            continue
        seen.add(ch)
        ordered.append(ch)
    return ordered


def _sample_snippets(samples: Sequence[dict[str, str]], *, limit: int = 5) -> list[dict[str, str]]:
    snippets: list[dict[str, str]] = []
    for sample in samples[:limit]:
        text = (sample.get("text") or "").strip().replace("\n", " ")
        snippets.append(
            {
                "channel": sample.get("channel") or "unknown",
                "preview": (text[:180] + ("…" if len(text) > 180 else "")),
            }
        )
    return snippets


def _format_chunk_attribution(channels: Sequence[str], post_count: int) -> str:
    names = ", ".join(channels) if channels else "unknown"
    return f"({names} - {post_count} posts)"


def _format_sample_block(
    samples: Sequence[dict[str, str]],
    start_index: int = 1,
) -> str:
    """Deprecated path — prefer format_sanitized_posts for discovery."""
    text, _ = format_sanitized_posts(samples, start_index=start_index)
    return text


def _chunk_samples(samples: List[dict[str, str]]) -> List[List[dict[str, str]]]:
    """Split attributed samples into chunks that fit discovery prompt budget."""
    max_posts = max(1, settings.discovery_chunk_posts)
    max_chars = max(2000, settings.discovery_chunk_chars)
    chunks: List[List[dict[str, str]]] = []
    current: List[dict[str, str]] = []
    current_chars = 0

    for sample in samples:
        # Budget against sanitized prompt size, not noisy enrichment blobs
        sanitized_line, _ = format_sanitized_posts([sample], start_index=1)
        piece_len = len(sanitized_line) + 8
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
        current.append(sample)
        current_chars += piece_len

    if current:
        chunks.append(current)
    return chunks or [[]]


def _merge_discovery_results(
    parts: List[CategoryDiscoveryResult],
) -> CategoryDiscoveryResult:
    categories: list[str] = []
    for part in parts:
        categories.extend(part.discovered_categories)
    return CategoryDiscoveryResult(
        discovered_categories=normalize_label_list(categories),
    )


async def discover_categories_from_samples(
    sample_texts: Sequence[SampleInput],
    channels: Optional[Sequence[str]] = None,
) -> CategoryDiscoveryResult:
    """
    Analyze sampled channel posts and return English job categories.
    Large sample sets are processed in chunks; each chunk is channel-attributed
    in SSE logs and persisted to debug_samples.json.
    Short/link-only samples are enriched with fetched page metadata first.
    Raises RuntimeError if Ollama fails for every chunk.
    """
    channel_list = [str(c).strip() for c in (channels or []) if str(c).strip()]
    attributed = _normalize_samples(sample_texts, channel_list)

    if not attributed:
        await broadcast_log(
            "CALLING_OLLAMA",
            "No sample posts available for category discovery.",
            {"sample_count": 0, "channels": channel_list},
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
                "discovered_categories": [],
                "enriched_metadata": [],
                "note": "No sample posts collected",
            }
        )
        raise ValueError(
            "No text posts were collected from the selected channels for discovery."
        )

    enriched_samples: List[dict[str, str]] = []
    enriched_metadata: list[dict] = []
    enrich_budget = 30
    for sample in attributed:
        text = sample.get("text") or ""
        channel = sample.get("channel") or "unknown"
        urls_in_text = extract_urls(text, include_telegram=True)
        if enrich_budget > 0 and should_enrich_message(text, extra_urls=urls_in_text):
            enriched, meta = await enrich_message_with_link_metadata(
                text,
                extra_urls=urls_in_text,
                force=True,
                log=True,
            )
            enriched_samples.append({"channel": channel, "text": enriched})
            if meta:
                enriched_metadata.append(
                    {
                        "channel": channel,
                        "original_preview": text[:240],
                        "metadata": meta,
                    }
                )
            enrich_budget -= 1
        else:
            enriched_samples.append({"channel": channel, "text": text})

    system = DISCOVERY_SYSTEM_PROMPT
    schema = CategoryDiscoveryResult.model_json_schema()
    chunks = _chunk_samples(enriched_samples)
    total_chunks = len(chunks)

    await broadcast_log(
        "CALLING_OLLAMA",
        f"Extracting categories with Gemma 2 from {len(enriched_samples)} sample posts "
        f"across {len(_channels_in_samples(enriched_samples))} channel(s) "
        f"in {total_chunks} chunk(s)...",
        {
            "sample_count": len(enriched_samples),
            "chunk_count": total_chunks,
            "channels": channel_list or _channels_in_samples(enriched_samples),
            "enriched_link_posts": len(enriched_metadata),
            "model": settings.ollama_model,
        },
    )

    results: List[CategoryDiscoveryResult] = []
    chunk_errors: list[str] = []
    chunk_debug: list[dict] = []
    prompt_parts: list[str] = []
    raw_parts: list[str] = []
    sample_index = 1

    for idx, chunk in enumerate(chunks, start=1):
        channels_in_chunk = _channels_in_samples(chunk)
        post_count = len(chunk)
        attribution = _format_chunk_attribution(channels_in_chunk, post_count)
        snippets = _sample_snippets(chunk)
        chunk_meta_base = {
            "chunk_index": idx,
            "total_chunks": total_chunks,
            "chunk_id": f"{idx}/{total_chunks}",
            "channels": channels_in_chunk,
            "channels_in_chunk": channels_in_chunk,
            "post_count": post_count,
            "sample_snippets": snippets,
        }

        sanitized_block, sanitized_items = format_sanitized_posts(
            chunk, start_index=sample_index
        )
        user = (
            "Analyze the sanitized post titles and English URL slugs below.\n"
            f"Channels in this chunk: {', '.join(channels_in_chunk) or 'unknown'} "
            f"(chunk {idx}/{total_chunks}).\n\n"
            f"Input Items:\n{sanitized_block}\n\n"
            "Return discovered_categories as high-level English job categories. "
            "Never return an empty list when posts have clear titles or URL slugs."
        )
        sample_index += post_count

        try:
            raw, raw_content, prompt_sent = await _ollama_chat_json(system, user, schema)
            # Tolerate legacy model output that still includes suggested_titles
            if isinstance(raw, dict) and "suggested_titles" in raw:
                raw = {k: v for k, v in raw.items() if k != "suggested_titles"}
            part = CategoryDiscoveryResult.model_validate(raw)
            part.discovered_categories = normalize_label_list(part.discovered_categories)

            used_fallback = False
            if not part.discovered_categories:
                fallback_cats = infer_categories_from_sanitized(sanitized_items)
                if fallback_cats:
                    part.discovered_categories = fallback_cats
                    used_fallback = True
                    channel_label = ", ".join(channels_in_chunk) or "unknown"
                    await broadcast_log(
                        "FALLBACK_ENGINE",
                        f"[Fallback Engine] Derived {len(fallback_cats)} categories "
                        f"from URL slugs for {channel_label}.",
                        {
                            **chunk_meta_base,
                            "categories": fallback_cats,
                            "sanitized_posts": [
                                {
                                    "title": s.get("title"),
                                    "slug": s.get("slug"),
                                    "hints": s.get("category_hints"),
                                }
                                for s in sanitized_items[:12]
                            ],
                        },
                    )

            results.append(part)
            prompt_parts.append(prompt_sent)
            raw_parts.append(raw_content)

            cats = part.discovered_categories
            empty = len(cats) == 0
            chunk_record = {
                **chunk_meta_base,
                "posts_in_chunk": post_count,
                "prompt_chars": len(prompt_sent),
                "prompt_sent_to_ollama": prompt_sent,
                "raw_prompt_sent": prompt_sent,
                "raw_ollama_response": raw_content,
                "raw_ai_response": raw_content,
                "parsed_categories": cats,
                "extracted_categories": cats,
                "sanitized_posts": sanitized_items,
                "used_fallback": used_fallback,
                "empty_result": empty,
                "error": None,
            }
            chunk_debug.append(chunk_record)

            await save_debug_sample(
                {
                    "kind": "discovery_chunk",
                    "chunk_id": f"{idx}/{total_chunks}",
                    "channels_in_chunk": channels_in_chunk,
                    "post_count": post_count,
                    "sample_snippets": snippets,
                    "sanitized_posts": [
                        {
                            "title": s.get("title"),
                            "slug": s.get("slug"),
                            "category_hints": s.get("category_hints"),
                        }
                        for s in sanitized_items
                    ],
                    "raw_prompt_sent": prompt_sent,
                    "raw_ai_response": raw_content,
                    "extracted_categories": cats,
                    "used_fallback": used_fallback,
                    "empty_result": empty,
                    "model": settings.ollama_model,
                }
            )

            if cats:
                cat_preview = ", ".join(cats)
                source = " (slug fallback)" if used_fallback else ""
                msg = (
                    f"Chunk {idx}/{total_chunks} {attribution} discovered "
                    f"{len(cats)} categories{source}: [{cat_preview}]"
                )
            else:
                msg = (
                    f"Chunk {idx}/{total_chunks} {attribution} returned "
                    f"0 categories."
                )
            await broadcast_log(
                "CALLING_OLLAMA",
                msg,
                {
                    **chunk_meta_base,
                    "categories": cats,
                    "used_fallback": used_fallback,
                    "empty_result": empty,
                    "expandable": empty,
                },
            )
        except (httpx.HTTPError, json.JSONDecodeError, ValidationError, KeyError) as exc:
            logger.exception("Category discovery chunk %s failed: %s", idx, exc)
            chunk_errors.append(f"chunk {idx}: {exc}")
            prompt_sent = _build_messages(system, user, schema)[1]

            # Even on Ollama failure, try deterministic slug fallback
            fallback_cats = infer_categories_from_sanitized(sanitized_items)
            used_fallback = bool(fallback_cats)
            if fallback_cats:
                results.append(
                    CategoryDiscoveryResult(discovered_categories=fallback_cats)
                )
                channel_label = ", ".join(channels_in_chunk) or "unknown"
                await broadcast_log(
                    "FALLBACK_ENGINE",
                    f"[Fallback Engine] Derived {len(fallback_cats)} categories "
                    f"from URL slugs for {channel_label} after Ollama error.",
                    {
                        **chunk_meta_base,
                        "categories": fallback_cats,
                        "error": str(exc),
                    },
                )

            chunk_record = {
                **chunk_meta_base,
                "posts_in_chunk": post_count,
                "error": str(exc),
                "prompt_sent_to_ollama": prompt_sent,
                "raw_prompt_sent": prompt_sent,
                "raw_ollama_response": "",
                "raw_ai_response": "",
                "parsed_categories": fallback_cats,
                "extracted_categories": fallback_cats,
                "sanitized_posts": sanitized_items,
                "used_fallback": used_fallback,
                "empty_result": not bool(fallback_cats),
            }
            chunk_debug.append(chunk_record)
            prompt_parts.append(prompt_sent)
            raw_parts.append("")

            await save_debug_sample(
                {
                    "kind": "discovery_chunk",
                    "chunk_id": f"{idx}/{total_chunks}",
                    "channels_in_chunk": channels_in_chunk,
                    "post_count": post_count,
                    "sample_snippets": snippets,
                    "sanitized_posts": [
                        {
                            "title": s.get("title"),
                            "slug": s.get("slug"),
                            "category_hints": s.get("category_hints"),
                        }
                        for s in sanitized_items
                    ],
                    "raw_prompt_sent": prompt_sent,
                    "raw_ai_response": "",
                    "extracted_categories": fallback_cats,
                    "used_fallback": used_fallback,
                    "empty_result": not bool(fallback_cats),
                    "error": str(exc),
                    "model": settings.ollama_model,
                }
            )
            if not fallback_cats:
                await broadcast_log(
                    "ERROR",
                    f"Chunk {idx}/{total_chunks} {attribution} failed: {exc}",
                    {
                        **chunk_meta_base,
                        "error": str(exc),
                        "empty_result": True,
                        "expandable": True,
                    },
                )

    merged = _merge_discovery_results(results)
    preview = [
        {
            "channel": s.get("channel"),
            "preview": (s.get("text") or "")[:400]
            + ("…" if len(s.get("text") or "") > 400 else ""),
        }
        for s in enriched_samples[:40]
    ]
    await save_debug_sample(
        {
            "kind": "discovery_summary",
            "channels_sampled": channel_list or _channels_in_samples(enriched_samples),
            "total_posts_collected": len(attributed),
            "sample_texts_preview": preview,
            "prompt_sent_to_ollama": "\n\n===== CHUNK SEPARATOR =====\n\n".join(prompt_parts),
            "raw_ollama_response": "\n\n===== CHUNK SEPARATOR =====\n\n".join(raw_parts),
            "chunk_count": total_chunks,
            "chunks": [
                {
                    "chunk_id": c.get("chunk_id"),
                    "channels_in_chunk": c.get("channels_in_chunk"),
                    "post_count": c.get("post_count"),
                    "extracted_categories": c.get("extracted_categories"),
                    "empty_result": c.get("empty_result"),
                    "error": c.get("error"),
                    "sample_snippets": c.get("sample_snippets"),
                }
                for c in chunk_debug
            ],
            "parsed_categories_count": len(merged.discovered_categories),
            "discovered_categories": merged.discovered_categories,
            "enriched_metadata": enriched_metadata,
            "chunk_errors": chunk_errors,
        }
    )

    if not results and chunk_errors:
        raise RuntimeError(
            "Ollama/Gemma 2 category discovery failed for all chunks: "
            + "; ".join(chunk_errors)
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

    system = EXTRACTION_SYSTEM_PROMPT
    user = (
        "Extract the job posting below. Translate Armenian/Russian first. "
        "Short title+link posts are valid job ads.\n\n"
        f"Message:\n\n{message_text[:6000]}"
    )
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

    job.title = (job.title or "").strip() or "Untitled Role"
    job.category = (job.category or "").strip() or "Uncategorized"
    job.translated_summary = (job.translated_summary or "").strip()
    job.apply_links = [
        u.strip()
        for u in (job.apply_links or [])
        if u and u.strip() and u.strip().lower().startswith(("http://", "https://"))
    ]
    # Empty apply_links is OK — scraper injects Telegram message URL fallback
    return job
