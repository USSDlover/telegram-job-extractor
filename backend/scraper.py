"""Telethon client helpers for sampling and scraping Telegram channels."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import List

from telethon import TelegramClient
from telethon.errors import ChannelInvalidError, ChannelPrivateError, UsernameInvalidError
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.types import Message

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
    entity = await client.get_entity(channel)
    try:
        await client(JoinChannelRequest(entity))
    except Exception as exc:  # already joined / not joinable as channel
        logger.debug("JoinChannel skipped for %s: %s", channel, exc)
    return entity


def _message_text(msg: Message) -> str:
    return (msg.message or "").strip()


async def sample_channel_messages(
    channel_username: str,
    limit: int | None = None,
) -> List[str]:
    """Fetch recent text posts from a channel for category discovery."""
    channel = _normalize_channel(channel_username)
    limit = limit or settings.sample_limit
    texts: List[str] = []

    try:
        async with telegram_client() as client:
            entity = await _ensure_joined(client, channel)
            async for msg in client.iter_messages(entity, limit=limit * 2):
                text = _message_text(msg)
                if text:
                    texts.append(text)
                if len(texts) >= limit:
                    break
    except (ChannelPrivateError, ChannelInvalidError, UsernameInvalidError) as exc:
        raise ValueError(f"Cannot access channel {channel}: {exc}") from exc

    return texts


async def discover_channel_categories(channel_username: str) -> dict:
    samples = await sample_channel_messages(channel_username)
    result = await discover_categories_from_samples(samples)
    return {
        "channel": _normalize_channel(channel_username),
        "discovered_categories": result.discovered_categories,
        "suggested_titles": result.suggested_titles,
        "sample_count": len(samples),
    }


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

    # If both filters provided, require both; if only one set, that one must match
    if cats and titles:
        return cat_ok and title_ok
    if cats:
        return cat_ok
    return title_ok


async def scrape_and_process_channel(
    channel_username: str,
    target_categories: List[str],
    target_titles: List[str],
) -> None:
    """
    Background task: iterate channel posts, extract jobs via Gemma 2,
    filter by selection, and persist matches to jobs.json.
    """
    channel = _normalize_channel(channel_username)
    logger.info(
        "Starting extraction for %s (categories=%s, titles=%s)",
        channel,
        target_categories,
        target_titles,
    )
    batch: list[dict] = []

    try:
        async with telegram_client() as client:
            entity = await _ensure_joined(client, channel)
            async for msg in client.iter_messages(entity, limit=settings.scrape_limit):
                text = _message_text(msg)
                if not text:
                    continue
                try:
                    extracted = await extract_job_data(text)
                except Exception as exc:
                    logger.warning("Skipping message %s: %s", msg.id, exc)
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
                batch.append(record)

                # Flush periodically to reduce data loss on interrupt
                if len(batch) >= 5:
                    await upsert_jobs(batch)
                    batch = []

        if batch:
            inserted = await upsert_jobs(batch)
            logger.info("Extraction finished for %s (last flush inserted~%s)", channel, inserted)
        else:
            logger.info("Extraction finished for %s (no pending batch)", channel)
    except Exception as exc:
        logger.exception("scrape_and_process_channel failed for %s: %s", channel, exc)
        if batch:
            try:
                await upsert_jobs(batch)
            except Exception:
                logger.exception("Failed to flush remaining jobs after error")
