"""Async Open Graph / HTML metadata fetcher for link-only Telegram posts."""

from __future__ import annotations

import logging
import re
from typing import Any, Sequence
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from activity_log import broadcast_log

logger = logging.getLogger(__name__)

URL_RE = re.compile(
    r"https?://[^\s<>\[\]()\"']+",
    re.IGNORECASE,
)

_SKIP_HOST_FRAGMENTS = (
    "t.me",
    "telegram.me",
    "telegram.org",
)

# Armenian / regional job boards — fetch deeper page content
JOB_BOARD_HOST_FRAGMENTS = (
    "job.am",
    "staff.am",
    "ijob.am",
    "careercenter.am",
    "list.am",
    "hh.ru",
    "hh.am",
    "linkedin.com",
    "greenhouse.io",
    "lever.co",
    "workable.com",
    "ashbyhq.com",
)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; TelegramJobExtractor/1.0; "
        "+https://localhost) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,hy;q=0.8,ru;q=0.7",
}

SHORT_MESSAGE_CHARS = 150
MAX_LINKS_PER_MESSAGE = 3
MAX_DESC_CHARS = 1200
MAX_PAGE_TEXT_CHARS = 3500

_ARMENIAN_RE = re.compile(r"[\u0530-\u058F]")
_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")


def is_short_non_english(text: str, *, max_chars: int = 280) -> bool:
    """True for brief Armenian / Russian captions that need translation + enrichment."""
    stripped = (text or "").strip()
    if not stripped or len(stripped) > max_chars:
        return False
    return bool(_ARMENIAN_RE.search(stripped) or _CYRILLIC_RE.search(stripped))


def is_telegram_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(skip in host for skip in _SKIP_HOST_FRAGMENTS)


def is_job_board_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(frag in host for frag in JOB_BOARD_HOST_FRAGMENTS)


def extract_urls(text: str, *, include_telegram: bool = False) -> list[str]:
    """Return unique http(s) URLs from plain message text."""
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for match in URL_RE.findall(text):
        url = match.rstrip(".,;:!?)]}>'\"")
        if not include_telegram and is_telegram_url(url):
            continue
        if url in seen:
            continue
        seen.add(url)
        found.append(url)
    return found


def partition_urls(urls: Sequence[str]) -> tuple[list[str], list[str]]:
    """Split into (external_urls, telegram_urls), deduped, order preserved."""
    external: list[str] = []
    telegram: list[str] = []
    seen: set[str] = set()
    for raw in urls:
        url = (raw or "").strip().rstrip(".,;:!?)]}>'\"")
        if not url or not url.lower().startswith(("http://", "https://")):
            continue
        if url in seen:
            continue
        seen.add(url)
        if is_telegram_url(url):
            telegram.append(url)
        else:
            external.append(url)
    return external, telegram


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


def _extract_main_page_text(soup: BeautifulSoup) -> str:
    """Pull readable body text for job board pages."""
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "nav", "footer", "header"]):
        tag.decompose()
    root = (
        soup.find("article")
        or soup.find("main")
        or soup.find(attrs={"role": "main"})
        or soup.find("div", class_=re.compile(r"(job|vacanc|content|description|detail)", re.I))
        or soup.body
        or soup
    )
    text = _clean_text(root.get_text(" ", strip=True))
    return text[:MAX_PAGE_TEXT_CHARS]


async def fetch_url_metadata(url: str, *, timeout: float = 8.0) -> dict[str, Any]:
    """
    Fetch Open Graph / HTML metadata for a URL.
    For known job boards, also scrape deeper page body text.
    Never raises — returns a dict with ok=False on failure.
    """
    result: dict[str, Any] = {
        "url": url,
        "ok": False,
        "title": "",
        "description": "",
        "headings": [],
        "page_text": "",
        "deep": False,
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
            html = resp.text[:700_000]
            final_url = str(resp.url)
    except httpx.TimeoutException:
        result["error"] = "timeout"
        return result
    except httpx.HTTPError as exc:
        result["error"] = str(exc)
        return result
    except Exception as exc:
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
        for tag_name in ("h1", "h2"):
            for heading in soup.find_all(tag_name, limit=4):
                text = _clean_text(heading.get_text(" ", strip=True))
                if text and text not in headings:
                    headings.append(text[:200])
            if len(headings) >= 2:
                break

        deep = is_job_board_url(final_url) or is_job_board_url(url)
        page_text = ""
        if deep or len(description) < 120:
            page_text = _extract_main_page_text(soup)

        result.update(
            {
                "ok": bool(title or description or headings or page_text),
                "title": title[:300],
                "description": description[:MAX_DESC_CHARS],
                "headings": headings[:6],
                "page_text": page_text,
                "deep": deep,
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
        lines.append(f"- URL: {meta.get('final_url') or meta.get('url')}")
        if meta.get("title"):
            lines.append(f"  - Page Title: {meta['title']}")
        if meta.get("description"):
            lines.append(f"  - Page Description: {meta['description']}")
        if meta.get("headings"):
            lines.append(f"  - Headings: {'; '.join(meta['headings'])}")
        if meta.get("page_text"):
            lines.append(f"  - Page Body: {meta['page_text'][:2500]}")
    return "\n".join(lines)


def should_enrich_message(text: str, extra_urls: Sequence[str] | None = None) -> bool:
    """Short teaser / link-only / brief HY-RU posts that need page metadata."""
    extra = [u for u in (extra_urls or []) if u]
    stripped = (text or "").strip()
    urls = extract_urls(stripped, include_telegram=True)
    has_urls = bool(urls or extra)

    # Brief Armenian/Russian title + link — always fetch OG / page context
    if is_short_non_english(stripped) and has_urls:
        return True

    if extra:
        # Hidden webpage/entity links often pair with short captions
        if not stripped or len(stripped) < 400:
            return True
    if not stripped:
        return bool(extra)
    if not urls and not extra:
        return False
    if len(stripped) < SHORT_MESSAGE_CHARS:
        return True
    without_urls = URL_RE.sub("", stripped).strip()
    return len(without_urls) < 80 and has_urls


async def enrich_message_with_link_metadata(
    text: str,
    *,
    extra_urls: Sequence[str] | None = None,
    force: bool = False,
    max_links: int = MAX_LINKS_PER_MESSAGE,
    log: bool = True,
) -> tuple[str, list[dict[str, Any]]]:
    """
    Fetch page metadata for plain-text and/or entity/webpage URLs and append it.
    Prefer external job-board URLs over t.me links. Failures never raise.
    """
    base = (text or "").strip()
    combined = list(dict.fromkeys([*extract_urls(base, include_telegram=True), *(extra_urls or [])]))
    external, telegram = partition_urls(combined)
    # Prefer scraping external destinations; only use telegram URLs if nothing else
    to_fetch = (external or telegram)[:max_links]

    if not to_fetch:
        return text, []
    if not force and not should_enrich_message(base, extra_urls=to_fetch):
        return text, []

    metadata_list: list[dict[str, Any]] = []
    for url in to_fetch:
        meta = await fetch_url_metadata(url)
        metadata_list.append(meta)
        if log:
            if meta.get("ok"):
                title = meta.get("title") or meta.get("description") or "untitled"
                kind = "deep" if meta.get("deep") else "preview"
                await broadcast_log(
                    "LINK_SCRAPER",
                    f'Fetched {kind} web content for {url} -> "{str(title)[:120]}"',
                    {"url": url, "title": meta.get("title"), "ok": True, "deep": meta.get("deep")},
                )
            else:
                await broadcast_log(
                    "LINK_SCRAPER",
                    f"Link preview failed for {url}: {meta.get('error')}",
                    {"url": url, "ok": False, "error": meta.get("error")},
                )

    ok_meta = [m for m in metadata_list if m.get("ok")]
    link_lines = ""
    if external:
        link_lines = "\n\n[Detected External Links]:\n" + "\n".join(f"- {u}" for u in external)

    if not ok_meta:
        if link_lines:
            return f"[Telegram Message]:\n{base or '(no text)'}{link_lines}", metadata_list
        return text, metadata_list

    enriched = (
        f"[Telegram Message]:\n{base or '(no text)'}"
        f"{link_lines}\n\n"
        f"{format_metadata_block(ok_meta)}"
    )
    return enriched, metadata_list
