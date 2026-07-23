"""Telethon client helpers for sampling and scraping Telegram channels."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import List, Sequence

from telethon import TelegramClient
from telethon.errors import ChannelInvalidError, ChannelPrivateError, UsernameInvalidError
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.types import Message

from activity_log import broadcast_log
from ai_extractor import discover_categories_from_samples, extract_job_data
from config import settings
from storage import upsert_jobs

logger = logging.getLogger(__name__)


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
    async for msg in client.iter_messages(entity, limit=limit * 2):
        text = _message_text(msg)
        if text:
            texts.append(text)
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


async def discover_channels_categories(channels: Sequence[str]) -> dict:
    """Sample multiple channels, aggregate texts, discover unified categories/titles."""
    channel_list = normalize_channels(channels)
    all_samples: List[str] = []
    per_channel: dict[str, int] = {}
    errors: list[str] = []

    await broadcast_log(
        "DISCOVER_STARTED",
        f"Discovery across {len(channel_list)} channel(s)...",
        {"channels": channel_list},
    )

    try:
        async with telegram_client() as client:
            for idx, channel in enumerate(channel_list, start=1):
                await broadcast_log(
                    "JOINING_TELEGRAM",
                    f"Processing channel {idx}/{len(channel_list)}: {channel}...",
                    {
                        "channel": channel,
                        "index": idx,
                        "total": len(channel_list),
                    },
                )
                try:
                    samples = await _sample_with_client(
                        client, channel, settings.sample_limit
                    )
                    per_channel[channel] = len(samples)
                    all_samples.extend(samples)
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

    if not all_samples and errors:
        raise ValueError("; ".join(errors))

    result = await discover_categories_from_samples(all_samples)
    await broadcast_log(
        "DISCOVERED_CATEGORIES",
        f"AI returned {len(result.discovered_categories)} categories and "
        f"{len(result.suggested_titles)} titles across {len(channel_list)} channel(s).",
        {
            "channels": channel_list,
            "discovered_categories": result.discovered_categories,
            "suggested_titles": result.suggested_titles,
            "sample_count": len(all_samples),
            "per_channel": per_channel,
            "errors": errors,
        },
    )
    return {
        "channels": channel_list,
        "discovered_categories": result.discovered_categories,
        "suggested_titles": result.suggested_titles,
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


async def _scrape_one_channel(
    client: TelegramClient,
    channel: str,
    target_categories: List[str],
    target_titles: List[str],
    channel_index: int,
    channel_total: int,
) -> tuple[int, int]:
    total_limit = settings.scrape_limit
    saved = 0
    processed = 0

    await broadcast_log(
        "EXTRACTION_STARTED",
        f"Processing channel {channel_index}/{channel_total}: {channel}...",
        {
            "channel": channel,
            "index": channel_index,
            "total": channel_total,
            "selected_categories": target_categories,
            "selected_titles": target_titles,
            "limit": total_limit,
        },
    )

    entity = await _ensure_joined(client, channel)
    await broadcast_log(
        "FETCHING_POSTS",
        f"Iterating up to {total_limit} messages from {channel}...",
        {"channel": channel, "limit": total_limit},
    )

    async for msg in client.iter_messages(entity, limit=total_limit):
        text = _message_text(msg)
        if not text:
            continue
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
            },
        )
        try:
            extracted = await extract_job_data(text)
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

        date_iso = msg.date.isoformat() if msg.date else None
        record = {
            "id": f"{channel.lstrip('@')}_{msg.id}",
            "channel": channel,
            "message_id": msg.id,
            "date": date_iso,
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
            },
        )

    await broadcast_log(
        "EXTRACTION_DONE",
        f"Finished {channel}: saved {saved} job(s); scanned {processed} text posts.",
        {
            "channel": channel,
            "saved": saved,
            "processed": processed,
            "index": channel_index,
            "total": channel_total,
        },
    )
    return saved, processed


async def scrape_and_process_channels(
    channels: Sequence[str],
    target_categories: List[str],
    target_titles: List[str],
) -> None:
    """Background task: extract jobs from multiple channels sequentially."""
    channel_list = normalize_channels(channels)
    total_saved = 0
    total_processed = 0

    await broadcast_log(
        "EXTRACTION_QUEUED",
        f"Multi-channel extraction starting for {len(channel_list)} channel(s).",
        {"channels": channel_list},
    )

    try:
        async with telegram_client() as client:
            for idx, channel in enumerate(channel_list, start=1):
                try:
                    saved, processed = await _scrape_one_channel(
                        client,
                        channel,
                        target_categories,
                        target_titles,
                        idx,
                        len(channel_list),
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

        await broadcast_log(
            "EXTRACTION_DONE",
            f"All channels finished. Saved {total_saved} job(s) across "
            f"{len(channel_list)} channel(s); scanned {total_processed} text posts.",
            {
                "channels": channel_list,
                "saved": total_saved,
                "processed": total_processed,
            },
        )
    except Exception as exc:
        logger.exception("scrape_and_process_channels failed: %s", exc)
        await broadcast_log(
            "ERROR",
            f"Multi-channel extraction failed: {exc}",
            {"channels": channel_list},
        )


async def scrape_and_process_channel(
    channel_username: str,
    target_categories: List[str],
    target_titles: List[str],
) -> None:
    """Backward-compatible single-channel extraction."""
    await scrape_and_process_channels(
        [channel_username], target_categories, target_titles
    )
