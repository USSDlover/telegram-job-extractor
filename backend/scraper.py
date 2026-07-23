"""Telethon client helpers for sampling and scraping Telegram channels."""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Optional, Sequence

from telethon import TelegramClient
from telethon.errors import ChannelInvalidError, ChannelPrivateError, UsernameInvalidError
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.types import (
    Message,
    MessageEntityTextUrl,
    MessageEntityUrl,
    MessageMediaWebPage,
)

from activity_log import broadcast_log
from ai_extractor import discover_categories_from_samples, extract_job_data
from config import settings
from link_preview import (
    enrich_message_with_link_metadata,
    partition_urls,
)
from storage import upsert_jobs

logger = logging.getLogger(__name__)

# Shared cancellation flag for the active extraction background task
_stop_scraper_event = asyncio.Event()


def reset_stop_flag() -> None:
    """Clear stop signal before starting a new extraction run."""
    _stop_scraper_event.clear()


def request_stop_extraction() -> bool:
    """
    Signal the running scraper to halt.
    Returns True if a stop was newly requested.
    """
    already = _stop_scraper_event.is_set()
    _stop_scraper_event.set()
    return not already


def is_stop_requested() -> bool:
    return _stop_scraper_event.is_set()


def _normalize_channel(channel_username: str) -> str:
    name = (channel_username or "").strip()
    if not name:
        raise ValueError("Channel username is required")
    if name.startswith("https://t.me/"):
        name = name.rstrip("/").split("/")[-1]
    if not name.startswith("@"):
        name = f"@{name}"
    return name


def normalize_channels(channels: Sequence[str] | None, channel: str | None = None) -> List[str]:
    """Normalize a list of channels; also accept legacy single `channel`."""
    raw: list[str] = []
    if channels:
        raw.extend(channels)
    if channel and channel.strip():
        raw.append(channel)
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw:
        try:
            name = _normalize_channel(item)
        except ValueError:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(name)
    if not normalized:
        raise ValueError("At least one channel username is required")
    return normalized


@asynccontextmanager
async def telegram_client():
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        raise RuntimeError(
            "TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in the environment"
        )
    client = TelegramClient(
        settings.telegram_session,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise RuntimeError(
            "Telegram session is not authorized. Run a one-time login "
            "(e.g. `python -c \"from telethon...\"`) or start the client interactively once."
        )
    try:
        yield client
    finally:
        await client.disconnect()


async def _ensure_joined(client: TelegramClient, channel: str) -> object:
    await broadcast_log(
        "JOINING_TELEGRAM",
        f"Connecting to Telegram and resolving {channel}...",
        {"channel": channel},
    )
    entity = await client.get_entity(channel)
    try:
        await client(JoinChannelRequest(entity))
        await broadcast_log(
            "JOINING_TELEGRAM",
            f"Joined / confirmed access to {channel}.",
            {"channel": channel},
        )
    except Exception as exc:  # already joined / not joinable as channel
        logger.debug("JoinChannel skipped for %s: %s", channel, exc)
        await broadcast_log(
            "JOINING_TELEGRAM",
            f"Using existing access to {channel}.",
            {"channel": channel},
        )
    return entity


def _message_text(msg: Message) -> str:
    return (msg.message or "").strip()


def extract_all_urls_from_message(msg: Message) -> list[str]:
    """
    Collect URLs from plain text, rich entities (TextUrl/Url), and webpage previews.
    """
    found: list[str] = []
    seen: set[str] = set()

    def _add(url: str | None) -> None:
        if not url:
            return
        cleaned = str(url).strip().rstrip(".,;:!?)]}>'\"")
        if not cleaned.lower().startswith(("http://", "https://")):
            return
        if cleaned in seen:
            return
        seen.add(cleaned)
        found.append(cleaned)

    text = msg.message or ""
    for match in re.findall(r"https?://[^\s<>\[\]()\"']+", text, flags=re.IGNORECASE):
        _add(match)

    # Prefer Telethon's UTF-16-safe entity helper when available
    try:
        for entity, entity_text in msg.get_entities_text():
            if isinstance(entity, MessageEntityTextUrl):
                _add(getattr(entity, "url", None))
            elif isinstance(entity, MessageEntityUrl):
                _add(entity_text)
    except Exception:
        if msg.entities and text:
            for ent in msg.entities:
                if isinstance(ent, MessageEntityTextUrl):
                    _add(getattr(ent, "url", None))
                elif isinstance(ent, MessageEntityUrl):
                    try:
                        _add(text[ent.offset : ent.offset + ent.length])
                    except Exception:
                        pass

    media = msg.media
    if isinstance(media, MessageMediaWebPage) or (
        media is not None and hasattr(media, "webpage")
    ):
        webpage = getattr(media, "webpage", None)
        if webpage is not None:
            _add(getattr(webpage, "url", None))
            _add(getattr(webpage, "display_url", None))

    return found


def build_message_payload(msg: Message) -> tuple[str, list[str]]:
    """Return (display_text, all_urls) for enrichment / AI."""
    text = _message_text(msg)
    urls = extract_all_urls_from_message(msg)
    external, _tg = partition_urls(urls)
    if external and text:
        # Ensure hidden destination links are visible to Gemma even before scrape
        extras = "\n".join(external)
        if extras not in text:
            text = f"{text}\n\n[Embedded Links]:\n{extras}"
    elif external and not text:
        text = "[Embedded Links]:\n" + "\n".join(external)
    return text, urls


async def _sample_with_client(
    client: TelegramClient,
    channel: str,
    limit: int,
) -> List[str]:
    entity = await _ensure_joined(client, channel)
    await broadcast_log(
        "FETCHING_POSTS",
        f"Sampling up to {limit} recent text posts from {channel}...",
        {"channel": channel, "limit": limit},
    )
    texts: List[str] = []
    async for msg in client.iter_messages(entity, limit=limit * 3):
        text, urls = build_message_payload(msg)
        if not text and not urls:
            continue
        external, _ = partition_urls(urls)
        enriched, _meta = await enrich_message_with_link_metadata(
            text or "(no text)",
            extra_urls=urls,
            force=bool(external) and len((text or "").strip()) < 400,
            log=False,
        )
        texts.append(enriched)
        if len(texts) >= limit:
            break
    await broadcast_log(
        "FETCHING_POSTS",
        f"Fetched {len(texts)} recent posts from {channel}.",
        {"channel": channel, "count": len(texts)},
    )
    return texts


async def sample_channel_messages(
    channel_username: str,
    limit: int | None = None,
) -> List[str]:
    """Fetch recent text posts from a channel for category discovery."""
    channel = _normalize_channel(channel_username)
    limit = limit or settings.sample_limit
    try:
        async with telegram_client() as client:
            return await _sample_with_client(client, channel, limit)
    except (ChannelPrivateError, ChannelInvalidError, UsernameInvalidError) as exc:
        await broadcast_log(
            "ERROR",
            f"Cannot access channel {channel}: {exc}",
            {"channel": channel},
        )
        raise ValueError(f"Cannot access channel {channel}: {exc}") from exc


async def sample_channel_categories(
    channels: Sequence[str],
    sample_limit_per_channel: int = 15,
) -> dict:
    """
    Sample recent posts from each channel, enrich short link posts, and discover
    unified English categories/titles via Gemma 2.
    """
    return await discover_channels_categories(
        channels,
        sample_limit_per_channel=sample_limit_per_channel,
    )


async def discover_channels_categories(
    channels: Sequence[str],
    sample_limit_per_channel: int | None = None,
) -> dict:
    """Sample multiple channels, aggregate texts, discover unified categories/titles."""
    channel_list = normalize_channels(channels)
    limit = sample_limit_per_channel or settings.sample_limit
    # Prefer a slightly smaller default for multi-channel discovery responsiveness
    if sample_limit_per_channel is None and len(channel_list) > 3:
        limit = min(limit, 15)
    all_samples: List[dict] = []
    per_channel: dict[str, int] = {}
    errors: list[str] = []

    await broadcast_log(
        "DISCOVER_STARTED",
        f"Discovery across {len(channel_list)} channel(s) "
        f"({limit} sample posts per channel)...",
        {"channels": channel_list, "sample_limit_per_channel": limit},
    )

    try:
        async with telegram_client() as client:
            for idx, channel in enumerate(channel_list, start=1):
                await broadcast_log(
                    "JOINING_TELEGRAM",
                    f"Fetching sample posts from channel {idx}/{len(channel_list)}: {channel}...",
                    {
                        "channel": channel,
                        "index": idx,
                        "total": len(channel_list),
                    },
                )
                try:
                    samples = await _sample_with_client(client, channel, limit)
                    per_channel[channel] = len(samples)
                    all_samples.extend(
                        {"channel": channel, "text": text} for text in samples
                    )
                except (
                    ChannelPrivateError,
                    ChannelInvalidError,
                    UsernameInvalidError,
                    ValueError,
                ) as exc:
                    errors.append(f"{channel}: {exc}")
                    await broadcast_log(
                        "ERROR",
                        f"Skipping {channel}: {exc}",
                        {"channel": channel},
                    )
    except Exception as exc:
        await broadcast_log("ERROR", f"Discovery failed: {exc}", {"channels": channel_list})
        raise

    if not all_samples:
        detail = "; ".join(errors) if errors else "no text posts found"
        raise ValueError(f"Could not collect sample posts for discovery ({detail})")

    await broadcast_log(
        "FETCHING_POSTS",
        f"Collected {len(all_samples)} sample posts across {len(channel_list)} channel(s) "
        f"({', '.join(f'{ch}:{n}' for ch, n in per_channel.items())}).",
        {"sample_count": len(all_samples), "per_channel": per_channel, "channels": channel_list},
    )

    result = await discover_categories_from_samples(
        all_samples, channels=channel_list
    )
    await broadcast_log(
        "DISCOVERED_CATEGORIES",
        f"AI returned {len(result.discovered_categories)} categories "
        f"across {len(channel_list)} channel(s).",
        {
            "channels": channel_list,
            "discovered_categories": result.discovered_categories,
            "sample_count": len(all_samples),
            "per_channel": per_channel,
            "errors": errors,
        },
    )
    return {
        "success": True,
        "channels": channel_list,
        "discovered_categories": result.discovered_categories,
        "sample_count": len(all_samples),
        "per_channel": per_channel,
        "errors": errors,
    }


# Backward-compatible alias
async def discover_channel_categories(channel_username: str) -> dict:
    result = await discover_channels_categories([channel_username])
    result["channel"] = result["channels"][0] if result["channels"] else channel_username
    return result


def _matches_filters(
    title: str,
    category: str,
    target_categories: List[str],
    target_titles: List[str],
) -> bool:
    title_l = title.lower()
    category_l = category.lower()
    cats = [c.lower() for c in target_categories if c]
    titles = [t.lower() for t in target_titles if t]

    if not cats and not titles:
        return True

    cat_ok = (not cats) or any(c == category_l or c in category_l for c in cats)
    title_ok = (not titles) or any(t == title_l or t in title_l for t in titles)

    if cats and titles:
        return cat_ok and title_ok
    if cats:
        return cat_ok
    return title_ok


def telegram_message_url(channel: str, message_id: int | str) -> str:
    """Build canonical public Telegram message URL."""
    clean = (channel or "").strip().lstrip("@")
    if clean.startswith("https://t.me/"):
        clean = clean.rstrip("/").split("/")[-1]
    return f"https://t.me/{clean}/{message_id}"


def resolve_apply_links(
    ai_links: list[str] | None,
    message_urls: list[str] | None,
    telegram_fallback: str,
) -> list[str]:
    """
    Prefer external apply URLs from AI + Telethon entities/webpage.
    Use t.me post URL only when no external HTTP link exists.
    """
    combined: list[str] = []
    for u in list(ai_links or []) + list(message_urls or []):
        if u and str(u).strip():
            combined.append(str(u).strip())
    external, _telegram = partition_urls(combined)
    if external:
        return external
    if telegram_fallback:
        return [telegram_fallback]
    return []


def _aware_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def _scrape_one_channel(
    client: TelegramClient,
    channel: str,
    target_categories: List[str],
    target_titles: List[str],
    channel_index: int,
    channel_total: int,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> tuple[int, int]:
    total_limit = settings.scrape_limit
    saved = 0
    processed = 0
    skipped_out_of_range = 0
    start_date = _aware_utc(start_date)
    end_date = _aware_utc(end_date)

    range_label = "all available history (limit capped)"
    if start_date or end_date:
        start_s = start_date.date().isoformat() if start_date else "…"
        end_s = end_date.date().isoformat() if end_date else "…"
        range_label = f"{start_s} → {end_s}"

    await broadcast_log(
        "EXTRACTION_STARTED",
        f"Processing channel {channel_index}/{channel_total}: {channel} "
        f"(posts between {range_label})...",
        {
            "channel": channel,
            "index": channel_index,
            "total": channel_total,
            "selected_categories": target_categories,
            "selected_titles": target_titles,
            "limit": total_limit,
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
        },
    )

    entity = await _ensure_joined(client, channel)
    await broadcast_log(
        "FETCHING_POSTS",
        f"[{channel}] Fetching posts between {range_label}...",
        {"channel": channel, "limit": total_limit},
    )

    async for msg in client.iter_messages(entity, limit=total_limit):
        if is_stop_requested():
            await broadcast_log(
                "EXTRACTION_STOPPED",
                f"[{channel}] Stop signal received. Halting extraction...",
                {"channel": channel, "processed": processed, "saved": saved},
            )
            break

        if not msg.date:
            continue
        msg_dt = _aware_utc(msg.date)
        if msg_dt is None:
            continue

        # Newest-first iteration: stop once we pass the lower bound
        if start_date and msg_dt < start_date:
            await broadcast_log(
                "FETCHING_POSTS",
                f"[{channel}] Reached posts older than {start_date.date().isoformat()}. "
                "Stopping scraper for channel.",
                {"channel": channel, "start_date": start_date.isoformat()},
            )
            break

        if end_date and msg_dt > end_date:
            skipped_out_of_range += 1
            continue

        text, message_urls = build_message_payload(msg)
        if not text and not message_urls:
            continue

        if is_stop_requested():
            await broadcast_log(
                "EXTRACTION_STOPPED",
                f"[{channel}] Stop signal received before Ollama call. Halting...",
                {"channel": channel, "message_id": msg.id},
            )
            break

        processed += 1
        await broadcast_log(
            "EXTRACTION_PROGRESS",
            f"[{channel}] Processing post {processed}/{total_limit} through Ollama...",
            {
                "channel": channel,
                "current": processed,
                "total": total_limit,
                "message_id": msg.id,
                "channel_index": channel_index,
                "channel_total": channel_total,
                "detected_urls": message_urls[:5],
            },
        )
        try:
            enriched_text, link_meta = await enrich_message_with_link_metadata(
                text or "(no text)",
                extra_urls=message_urls,
                force=bool(partition_urls(message_urls)[0]),
            )
            if is_stop_requested():
                await broadcast_log(
                    "EXTRACTION_STOPPED",
                    f"[{channel}] Stop signal received after link preview. Halting...",
                    {"channel": channel},
                )
                break
            if link_meta:
                await broadcast_log(
                    "LINK_SCRAPER",
                    f"[{channel}] Enriched message {msg.id} with "
                    f"{len([m for m in link_meta if m.get('ok')])} link preview(s).",
                    {"message_id": msg.id, "channel": channel},
                )
            extracted = await extract_job_data(enriched_text)
        except Exception as exc:
            logger.warning("Skipping message %s: %s", msg.id, exc)
            await broadcast_log(
                "ERROR",
                f"Failed extracting message {msg.id}: {exc}",
                {"message_id": msg.id, "channel": channel},
            )
            continue
        if not extracted:
            continue
        if not _matches_filters(
            extracted.title,
            extracted.category,
            target_categories,
            target_titles,
        ):
            continue

        tg_url = telegram_message_url(channel, msg.id)
        extracted.apply_links = resolve_apply_links(
            extracted.apply_links,
            message_urls,
            tg_url,
        )

        date_iso = msg_dt.isoformat()
        record = {
            "id": f"{channel.lstrip('@')}_{msg.id}",
            "channel": channel,
            "message_id": msg.id,
            "date": date_iso,
            "telegram_url": tg_url,
            **extracted.model_dump(),
        }
        await upsert_jobs([record])
        saved += 1
        await broadcast_log(
            "JOB_SAVED",
            f"Extracted position: [{extracted.title}] from {channel}, saved to storage.",
            {
                "job": record,
                "title": extracted.title,
                "category": extracted.category,
                "channel": channel,
                "apply_links": extracted.apply_links,
                "telegram_url": tg_url,
            },
        )

    stopped = is_stop_requested()
    await broadcast_log(
        "EXTRACTION_STOPPED" if stopped else "EXTRACTION_DONE",
        (
            f"[{channel}] Extraction pipeline stopped. Saved {saved} job(s); "
            f"scanned {processed} text posts."
            if stopped
            else f"Finished {channel}: saved {saved} job(s); scanned {processed} text posts"
            + (
                f"; skipped {skipped_out_of_range} newer than end date"
                if skipped_out_of_range
                else ""
            )
            + "."
        ),
        {
            "channel": channel,
            "saved": saved,
            "processed": processed,
            "skipped_out_of_range": skipped_out_of_range,
            "index": channel_index,
            "total": channel_total,
            "stopped": stopped,
        },
    )
    return saved, processed


async def scrape_and_process_channels(
    channels: Sequence[str],
    target_categories: List[str],
    target_titles: List[str],
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> None:
    """Background task: extract jobs from multiple channels sequentially."""
    channel_list = normalize_channels(channels)
    total_saved = 0
    total_processed = 0
    start_date = _aware_utc(start_date)
    end_date = _aware_utc(end_date)
    reset_stop_flag()

    await broadcast_log(
        "EXTRACTION_QUEUED",
        f"Multi-channel extraction starting for {len(channel_list)} channel(s).",
        {
            "channels": channel_list,
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
        },
    )

    try:
        async with telegram_client() as client:
            for idx, channel in enumerate(channel_list, start=1):
                if is_stop_requested():
                    await broadcast_log(
                        "EXTRACTION_STOPPED",
                        "Stop signal received. Skipping remaining channels.",
                        {"channels": channel_list, "stopped_before": channel},
                    )
                    break
                try:
                    saved, processed = await _scrape_one_channel(
                        client,
                        channel,
                        target_categories,
                        target_titles,
                        idx,
                        len(channel_list),
                        start_date=start_date,
                        end_date=end_date,
                    )
                    total_saved += saved
                    total_processed += processed
                except Exception as exc:
                    logger.exception("Channel extraction failed for %s", channel)
                    await broadcast_log(
                        "ERROR",
                        f"Extraction failed for {channel}: {exc}",
                        {"channel": channel},
                    )
                if is_stop_requested():
                    break

        stopped = is_stop_requested()
        await broadcast_log(
            "EXTRACTION_STOPPED" if stopped else "EXTRACTION_DONE",
            (
                f"Extraction pipeline stopped. Saved {total_saved} job(s) across "
                f"{len(channel_list)} channel(s); scanned {total_processed} text posts."
                if stopped
                else f"All channels finished. Saved {total_saved} job(s) across "
                f"{len(channel_list)} channel(s); scanned {total_processed} text posts."
            ),
            {
                "channels": channel_list,
                "saved": total_saved,
                "processed": total_processed,
                "stopped": stopped,
            },
        )
    except Exception as exc:
        logger.exception("scrape_and_process_channels failed: %s", exc)
        await broadcast_log(
            "ERROR",
            f"Multi-channel extraction failed: {exc}",
            {"channels": channel_list},
        )
    finally:
        # Leave the flag set until the next run resets it, so late checks stay consistent
        pass


async def scrape_and_process_channel(
    channel_username: str,
    target_categories: List[str],
    target_titles: List[str],
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> None:
    """Backward-compatible single-channel extraction."""
    await scrape_and_process_channels(
        [channel_username],
        target_categories,
        target_titles,
        start_date=start_date,
        end_date=end_date,
    )
