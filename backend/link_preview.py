"""Async Open Graph / HTML metadata fetcher for link-only Telegram posts."""

from __future__ import annotations

import logging
import re
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from activity_log import broadcast_log

logger = logging.getLogger(__name__)

URL_RE = re.compile(
    r"https?://[^\s<>\[\]()\"']+",
    re.IGNORECASE,
)

# Skip obvious non-content / tracking hosts
_SKIP_HOST_FRAGMENTS = (
    "t.me",
    "telegram.me",
    "telegram.org",
)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; TelegramJobExtractor/1.0; "
        "+https://localhost) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

SHORT_MESSAGE_CHARS = 150
MAX_LINKS_PER_MESSAGE = 3
MAX_DESC_CHARS = 1200
MAX_HEADING_CHARS = 400


def extract_urls(text: str) -> list[str]:
    """Return unique external http(s) URLs from message text."""
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for match in URL_RE.findall(text):
        url = match.rstrip(".,;:!?)]}>'\"")
        host = (urlparse(url).hostname or "").lower()
        if any(skip in host for skip in _SKIP_HOST_FRAGMENTS):
            continue
        if url in seen:
            continue
        seen.add(url)
        found.append(url)
    return found


def _meta_content(soup: BeautifulSoup, *, prop: str | None = None, name: str | None = None) -> str:
    if prop:
        tag = soup.find("meta", attrs={"property": prop})
        if tag and tag.get("content"):
            return str(tag["content"]).strip()
    if name:
        tag = soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return str(tag["content"]).strip()
    return ""


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


async def fetch_url_metadata(url: str, *, timeout: float = 5.0) -> dict[str, Any]:
    """
    Fetch Open Graph / HTML metadata for a URL.
    Never raises — returns a dict with ok=False on failure.
    """
    result: dict[str, Any] = {
        "url": url,
        "ok": False,
        "title": "",
        "description": "",
        "headings": [],
        "error": None,
    }
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            headers=DEFAULT_HEADERS,
        ) as client:
            resp = await client.get(url)
            if resp.status_code >= 400:
                result["error"] = f"HTTP {resp.status_code}"
                return result
            content_type = (resp.headers.get("content-type") or "").lower()
            if "html" not in content_type and "text/" not in content_type:
                result["error"] = f"Unsupported content-type: {content_type or 'unknown'}"
                return result
            html = resp.text[:500_000]
            final_url = str(resp.url)
    except httpx.TimeoutException:
        result["error"] = "timeout"
        return result
    except httpx.HTTPError as exc:
        result["error"] = str(exc)
        return result
    except Exception as exc:  # anti-bot / unexpected
        result["error"] = str(exc)
        return result

    try:
        soup = BeautifulSoup(html, "html.parser")
        title = (
            _meta_content(soup, prop="og:title")
            or _meta_content(soup, name="twitter:title")
            or _clean_text(soup.title.get_text() if soup.title else "")
        )
        description = (
            _meta_content(soup, prop="og:description")
            or _meta_content(soup, name="description")
            or _meta_content(soup, name="twitter:description")
        )
        headings: list[str] = []
        if len(description) < 80:
            for tag_name in ("h1", "h2"):
                for heading in soup.find_all(tag_name, limit=3):
                    text = _clean_text(heading.get_text(" ", strip=True))
                    if text and text not in headings:
                        headings.append(text[:200])
                if headings:
                    break

        result.update(
            {
                "ok": bool(title or description or headings),
                "title": title[:300],
                "description": description[:MAX_DESC_CHARS],
                "headings": headings[:5],
                "final_url": final_url,
            }
        )
        if not result["ok"]:
            result["error"] = "No usable metadata found"
    except Exception as exc:
        result["error"] = f"parse_error: {exc}"
        logger.debug("Failed parsing metadata for %s: %s", url, exc)

    return result


def format_metadata_block(metadata_list: list[dict[str, Any]]) -> str:
    lines: list[str] = ["[Fetched Web Page Metadata]:"]
    for meta in metadata_list:
        if not meta.get("ok"):
            lines.append(f"- URL: {meta.get('url')} (fetch failed: {meta.get('error')})")
            continue
        lines.append(f"- URL: {meta.get('url')}")
        if meta.get("title"):
            lines.append(f"  - Page Title: {meta['title']}")
        if meta.get("description"):
            lines.append(f"  - Page Description: {meta['description']}")
        if meta.get("headings"):
            lines.append(f"  - Headings: {'; '.join(meta['headings'])}")
    return "\n".join(lines)


def should_enrich_message(text: str) -> bool:
    """Short teaser / link-only posts that need page metadata."""
    if not text or not text.strip():
        return False
    stripped = text.strip()
    urls = extract_urls(stripped)
    if not urls:
        return False
    # Always enrich very short posts; also enrich slightly longer teasers that are mostly a URL
    if len(stripped) < SHORT_MESSAGE_CHARS:
        return True
    # Medium teasers dominated by a URL (little prose beyond the link)
    without_urls = URL_RE.sub("", stripped).strip()
    return len(without_urls) < 80 and len(urls) >= 1


async def enrich_message_with_link_metadata(
    text: str,
    *,
    force: bool = False,
    max_links: int = MAX_LINKS_PER_MESSAGE,
    log: bool = True,
) -> tuple[str, list[dict[str, Any]]]:
    """
    If the message is short/link-heavy, fetch page metadata and append it.
    Returns (enriched_text, metadata_list). Failures never raise.
    """
    if not text:
        return text, []
    if not force and not should_enrich_message(text):
        return text, []

    urls = extract_urls(text)[:max_links]
    if not urls:
        return text, []

    metadata_list: list[dict[str, Any]] = []
    for url in urls:
        meta = await fetch_url_metadata(url)
        metadata_list.append(meta)
        if log:
            if meta.get("ok"):
                title = meta.get("title") or meta.get("description") or "untitled"
                await broadcast_log(
                    "LINK_SCRAPER",
                    f'Fetched web preview for {url} -> "{title[:120]}"',
                    {"url": url, "title": meta.get("title"), "ok": True},
                )
            else:
                await broadcast_log(
                    "LINK_SCRAPER",
                    f"Link preview failed for {url}: {meta.get('error')}",
                    {"url": url, "ok": False, "error": meta.get("error")},
                )

    ok_meta = [m for m in metadata_list if m.get("ok")]
    if not ok_meta:
        return text, metadata_list

    enriched = (
        f"[Telegram Message]:\n{text.strip()}\n\n"
        f"{format_metadata_block(ok_meta)}"
    )
    return enriched, metadata_list
