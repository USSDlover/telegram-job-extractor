"""Publish extracted jobs to a destination Telegram channel via Telethon."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Sequence
from urllib.parse import urlparse

from telethon.errors import (
    ChannelInvalidError,
    ChannelPrivateError,
    ChatWriteForbiddenError,
    FloodWaitError,
    UsernameInvalidError,
)

from time import monotonic

from telethon.tl.functions.channels import GetFullChannelRequest

from activity_log import broadcast_log
from ai_extractor import localize_job_for_publish, normalize_publish_language
from config import settings
from link_preview import partition_urls
from scraper import telegram_client
from storage import (
    get_admin_channels,
    get_default_admin_handles,
    get_jobs_by_ids,
    get_published_jobs,
    get_unpublished_jobs,
    mark_job_as_published,
)

_STATUS_TTL_SECONDS = 20.0
_STATS_TTL_SECONDS = 45.0
_ADMIN_CHANNELS_TTL_SECONDS = 30.0
_status_cache: dict[str, Any] = {"ts": 0.0, "value": None}
_stats_cache: dict[str, Any] = {"ts": 0.0, "value": None}
_admin_channels_cache: dict[str, Any] = {"ts": 0.0, "value": None}

logger = logging.getLogger(__name__)

_MD_SPECIALS = re.compile(r"([\\`*_\[\]])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")
_LANGUAGE_HASHTAGS = {
    "armenian": "#Armenia",
    "hy": "#Armenia",
    "հայերեն": "#Armenia",
    "russian": "#Russia",
    "ru": "#Russia",
    "русский": "#Russia",
    "english": "#English",
    "en": "#English",
}
_SKIP_HASHTAG_WORDS = {"and", "or", "the", "of", "a", "an", "to", "for", "in"}
_RTL_LANGUAGES = {"Persian", "Arabic"}
_LANGUAGE_BADGES = {
    "English": "🇬🇧 English",
    "Persian": "🇮🇷 فارسی",
    "Arabic": "🇸🇦 العربية",
}
_POST_LABELS = {
    "English": {
        "untitled": "Untitled Role",
        "uncategorized": "Uncategorized",
        "unknown_language": "Unknown",
        "category": "Category",
        "original_language": "Original language",
        "summary": "Summary",
        "links": "Links",
        "no_apply": "Application link not provided",
        "apply_via": "Apply via {host}",
        "apply_fallback": "Application link",
        "apply_fallback_n": "Application link {index}",
        "view_source": "View original Telegram post",
        "empty_summary": "Details are available via the application link.",
    },
    "Persian": {
        "untitled": "موقعیت بدون عنوان",
        "uncategorized": "بدون دسته",
        "unknown_language": "نامشخص",
        "category": "دسته‌بندی",
        "original_language": "زبان اصلی",
        "summary": "خلاصه",
        "links": "لینک‌ها",
        "no_apply": "لینک درخواست ارائه نشده است",
        "apply_via": "درخواست از طریق {host}",
        "apply_fallback": "لینک درخواست",
        "apply_fallback_n": "لینک درخواست {index}",
        "view_source": "مشاهده پست اصلی تلگرام",
        "empty_summary": "جزئیات از طریق لینک درخواست در دسترس است.",
    },
    "Arabic": {
        "untitled": "وظيفة بدون عنوان",
        "uncategorized": "غير مصنف",
        "unknown_language": "غير معروف",
        "category": "التصنيف",
        "original_language": "اللغة الأصلية",
        "summary": "الملخص",
        "links": "الروابط",
        "no_apply": "لم يتم توفير رابط التقديم",
        "apply_via": "التقديم عبر {host}",
        "apply_fallback": "رابط التقديم",
        "apply_fallback_n": "رابط التقديم {index}",
        "view_source": "عرض منشور تيليجرام الأصلي",
        "empty_summary": "التفاصيل متاحة عبر رابط التقديم.",
    },
}


def _looks_like_peer_id(value: str) -> bool:
    return bool(value) and value.lstrip("-").isdigit()


def resolve_publish_channel(channel_username: str | None = None) -> str:
    """Normalize an override or fall back to TELEGRAM_TARGET_CHANNEL."""
    raw = (channel_username or settings.telegram_target_channel or "").strip()
    if not raw:
        raise ValueError(
            "No publish target. Add a default channel in Manage Channels or pass target_channels."
        )
    if raw.startswith("https://t.me/"):
        raw = raw.rstrip("/").split("/")[-1]
    if _looks_like_peer_id(raw):
        return raw
    if not raw.startswith("@"):
        raw = f"@{raw}"
    return raw


def normalize_channel_list(target_channels: Sequence[str] | None = None) -> list[str]:
    """Normalize handles without applying env/storage fallbacks."""
    resolved: list[str] = []
    seen: set[str] = set()
    for raw in target_channels or []:
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            channel = resolve_publish_channel(text)
        except ValueError:
            continue
        key = channel.lower()
        if key in seen:
            continue
        seen.add(key)
        resolved.append(channel)
    return resolved


def resolve_publish_channels(target_channels: Sequence[str] | None = None) -> list[str]:
    """Normalize a list of destinations; fall back to TELEGRAM_TARGET_CHANNEL."""
    resolved = normalize_channel_list(target_channels)
    if resolved:
        return resolved
    return [resolve_publish_channel(None)]


async def resolve_publish_targets(target_channels: Sequence[str] | None = None) -> list[str]:
    """
    Normalize requested destinations. When the list is empty, use channels
    marked is_default in channels.json, then TELEGRAM_TARGET_CHANNEL.
    """
    resolved = normalize_channel_list(target_channels)
    if resolved:
        return resolved
    defaults = normalize_channel_list(await get_default_admin_handles())
    if defaults:
        return defaults
    return [resolve_publish_channel(None)]


def _entity_ref(channel: str):
    raw = str(channel or "").strip()
    if _looks_like_peer_id(raw):
        return int(raw)
    return resolve_publish_channel(raw)


def _posted_message_url(target: str, message_id: Any) -> str:
    if not message_id:
        return ""
    if str(target).startswith("@"):
        return f"https://t.me/{target.lstrip('@')}/{message_id}"
    digits = str(target).lstrip("-")
    if digits.startswith("100") and digits[3:].isdigit():
        digits = digits[3:]
    if digits.isdigit():
        return f"https://t.me/c/{digits}/{message_id}"
    return ""


def _can_post_to_channel(entity: Any) -> bool:
    if getattr(entity, "creator", False):
        return True
    rights = getattr(entity, "admin_rights", None)
    if rights is None:
        return False
    return bool(getattr(rights, "post_messages", False))


def _escape_md(text: str) -> str:
    return _MD_SPECIALS.sub(r"\\\1", text or "")


def _as_hashtag(token: str) -> str:
    cleaned = _NON_ALNUM.sub("", token or "")
    if not cleaned:
        return ""
    return "#" + cleaned[:1].upper() + cleaned[1:]


def build_hashtags(category: str | None, original_language: str | None) -> str:
    tags: list[str] = ["#Jobs"]
    for part in re.split(r"[&/,]| and ", category or ""):
        word = part.strip()
        if not word or word.lower() in _SKIP_HASHTAG_WORDS:
            continue
        tag = _as_hashtag(word)
        if tag and tag not in tags:
            tags.append(tag)
    lang_key = (original_language or "").strip().lower()
    lang_tag = _LANGUAGE_HASHTAGS.get(lang_key)
    if lang_tag and lang_tag not in tags:
        tags.append(lang_tag)
    return " ".join(tags)


def _unique_http_urls(urls: Sequence[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in urls or []:
        url = str(raw or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def _link_label(url: str, index: int, total: int, labels: dict[str, str] | None = None) -> str:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    copy = labels or _POST_LABELS["English"]
    if total == 1:
        return copy["apply_via"].format(host=host) if host else copy["apply_fallback"]
    return copy["apply_via"].format(host=host) if host else copy["apply_fallback_n"].format(index=index)


def format_job_post(job_data: dict[str, Any], language: str | None = None) -> str:
    """Render a professional Telegram Markdown post from a stored job record."""
    publish_language = normalize_publish_language(
        language or job_data.get("publish_language") or "English"
    )
    labels = _POST_LABELS.get(publish_language) or _POST_LABELS["English"]
    rtl = publish_language in _RTL_LANGUAGES
    mark = "\u200f" if rtl else ""

    title = (job_data.get("title") or "").strip() or labels["untitled"]
    company = (job_data.get("company") or "").strip()
    category = (job_data.get("category") or "").strip() or labels["uncategorized"]
    source_language = (job_data.get("original_language") or "").strip() or labels["unknown_language"]
    summary = re.sub(r"\s+", " ", (job_data.get("translated_summary") or "").strip())
    if len(summary) > 900:
        summary = summary[:897].rstrip() + "…"
    if not summary:
        summary = labels["empty_summary"]

    apply_links, telegram_links = partition_urls(
        _unique_http_urls(job_data.get("apply_links"))
    )
    telegram_url = (job_data.get("telegram_url") or "").strip()
    if telegram_url and telegram_url not in telegram_links:
        telegram_links.append(telegram_url)

    def line(text: str) -> str:
        return f"{mark}{text}" if mark else text

    lines = [
        line(_LANGUAGE_BADGES[publish_language]),
        line(f"💼 *{_escape_md(title)}*"),
    ]
    if company:
        lines.append(line(f"🏢 *{_escape_md(company)}*"))
    lines.extend(
        [
            "",
            line(f"🗂 *{labels['category']}:* {_escape_md(category)}"),
            line(f"🗣 *{labels['original_language']}:* {_escape_md(source_language)}"),
            "",
            line(f"📝 *{labels['summary']}*"),
            line(_escape_md(summary)),
            "",
            line(f"🔗 *{labels['links']}*"),
        ]
    )

    if apply_links:
        total = len(apply_links)
        for idx, url in enumerate(apply_links, start=1):
            label = _escape_md(_link_label(url, idx, total, labels))
            lines.append(line(f"• [{label}]({url})"))
    else:
        lines.append(line(f"• {labels['no_apply']}"))

    if telegram_links:
        source = telegram_links[0]
        lines.append(line(f"• [{_escape_md(labels['view_source'])}]({source})"))

    tags = build_hashtags(
        job_data.get("source_category") or (category if publish_language == "English" else ""),
        job_data.get("original_language"),
    )
    if publish_language == "Persian" and "#Persian" not in tags:
        tags = f"{tags} #Persian".strip()
    elif publish_language == "Arabic" and "#Arabic" not in tags:
        tags = f"{tags} #Arabic".strip()
    lines.extend(["", line(f"🏷 {tags}")])
    text = "\n".join(lines)
    return text[:4000]


async def _job_for_publish_language(
    job_data: dict[str, Any],
    language: str,
    *,
    already_localized: bool = False,
    republish: bool = False,
) -> dict[str, Any]:
    lang = normalize_publish_language(language)
    if already_localized:
        copy = dict(job_data or {})
        copy["publish_language"] = lang
        return copy
    if lang == "English":
        copy = dict(job_data or {})
        copy["publish_language"] = "English"
        return copy

    job_id = str((job_data or {}).get("id") or (job_data or {}).get("message_id") or "")
    title = str((job_data or {}).get("title") or "").strip() or "Untitled Role"
    reason = " for republication" if republish else ""
    await broadcast_log(
        "TRANSLATING",
        f"Translating [{title}] to {lang}{reason} with Gemma 2…",
        {"job_id": job_id, "language": lang, "title": title, "republish": republish},
    )
    localized = await localize_job_for_publish(job_data, lang)
    await broadcast_log(
        "TRANSLATION_DONE",
        f"Translated [{title}] to {lang}{reason}.",
        {"job_id": job_id, "language": lang, "title": title, "republish": republish},
    )
    return localized


async def publish_job_to_channel(
    job_data: dict[str, Any],
    channel_username: str,
    language: str = "English",
    *,
    already_localized: bool = False,
    republish: bool = False,
) -> dict[str, Any]:
    """
    Send a formatted job post to `channel_username` using the existing Telethon session.
    Returns metadata about the sent message. Raises on Telegram / validation errors.
    """
    if not job_data:
        raise ValueError("job_data is required")
    target = resolve_publish_channel(channel_username)
    publish_language = normalize_publish_language(language)
    localized = await _job_for_publish_language(
        job_data,
        publish_language,
        already_localized=already_localized,
        republish=republish,
    )
    job_id = str(localized.get("id") or localized.get("message_id") or "")
    title = (localized.get("title") or job_data.get("title") or "").strip() or "Untitled Role"
    body = format_job_post(localized, publish_language)
    verb = "Republishing" if republish else "Publishing"
    stage = "REPUBLISH_STARTED" if republish else "PUBLISH_STARTED"

    await broadcast_log(
        stage,
        f"{verb} [{title}] to {target} ({publish_language})...",
        {
            "job_id": job_id,
            "channel": target,
            "title": title,
            "language": publish_language,
            "republish": republish,
        },
    )

    try:
        sent = await _send_job_message(target, body)
    except (
        ChannelPrivateError,
        ChannelInvalidError,
        UsernameInvalidError,
        ChatWriteForbiddenError,
    ) as exc:
        logger.warning("Cannot publish to %s: %s", target, exc)
        raise RuntimeError(
            f"Cannot post to {target}. Confirm the account is a channel admin: {exc}"
        ) from exc
    except RuntimeError:
        raise
    except Exception as exc:
        logger.exception("publish_job_to_channel failed for %s", job_id)
        raise RuntimeError(f"Failed to publish job {job_id or title}: {exc}") from exc

    posted_id = getattr(sent, "id", None)
    posted_url = _posted_message_url(target, posted_id)
    done_verb = "Republished" if republish else "Published"
    await broadcast_log(
        "JOB_PUBLISHED",
        f"{done_verb} [{title}] to {target}.",
        {
            "job_id": job_id,
            "channel": target,
            "title": title,
            "language": publish_language,
            "posted_message_id": posted_id,
            "posted_url": posted_url,
            "republish": republish,
        },
    )
    return {
        "job_id": job_id,
        "channel": target,
        "language": publish_language,
        "posted_message_id": posted_id,
        "posted_url": posted_url,
    }


async def publish_job_to_channels(
    job_data: dict[str, Any],
    target_channels: Sequence[str] | None = None,
    delay_seconds: float | None = None,
    language: str = "English",
    republish: bool = False,
) -> dict[str, Any]:
    """
    Publish one job to each destination, with a short pause between channels.
    One failed destination is logged and skipped; others still run.
    """
    targets = await resolve_publish_targets(target_channels)
    publish_language = normalize_publish_language(language)
    delay = settings.telegram_channel_delay if delay_seconds is None else float(delay_seconds)
    delay = max(0.0, delay)
    job_id = str((job_data or {}).get("id") or (job_data or {}).get("message_id") or "")
    title = str((job_data or {}).get("title") or "").strip() or "Untitled Role"
    posted: list[dict[str, Any]] = []
    errors: list[str] = []
    localized = await _job_for_publish_language(
        job_data,
        publish_language,
        republish=republish,
    )
    verb = "Republishing" if republish else "Publishing"
    stage = "REPUBLISH_STARTED" if republish else "PUBLISH_STARTED"

    await broadcast_log(
        stage,
        f"{verb} [{title}] to {len(targets)} channel(s) in {publish_language}: {', '.join(targets)}.",
        {
            "job_id": job_id,
            "channels": targets,
            "title": title,
            "language": publish_language,
            "republish": republish,
        },
    )

    for index, channel in enumerate(targets, start=1):
        try:
            posted.append(
                await publish_job_to_channel(
                    localized,
                    channel,
                    language=publish_language,
                    already_localized=True,
                    republish=republish,
                )
            )
        except Exception as exc:
            errors.append(f"{channel}: {exc}")
            logger.warning("Publish to %s failed for %s: %s", channel, job_id, exc)
            await broadcast_log(
                "ERROR",
                f"Failed to {'republish' if republish else 'publish'} [{title}] to {channel}: {exc}",
                {
                    "job_id": job_id,
                    "channel": channel,
                    "title": title,
                    "republish": republish,
                },
            )
        if index < len(targets) and delay > 0:
            await asyncio.sleep(delay)

    ok_channels = [item.get("channel") for item in posted if item.get("channel")]
    if ok_channels:
        done_verb = "Republished" if republish else "Published"
        await broadcast_log(
            "JOB_PUBLISHED",
            f"{done_verb} job {job_id or title} to {len(ok_channels)} channel"
            f"{'' if len(ok_channels) == 1 else 's'} ({', '.join(ok_channels)}).",
            {
                "job_id": job_id,
                "channels": ok_channels,
                "title": title,
                "posted_url": posted[0].get("posted_url") if posted else "",
                "language": publish_language,
                "republish": republish,
            },
        )

    return {
        "job_id": job_id,
        "language": publish_language,
        "channels": ok_channels,
        "channel": ok_channels[0] if ok_channels else None,
        "posted": posted,
        "posted_url": posted[0].get("posted_url") if posted else "",
        "posted_message_id": posted[0].get("posted_message_id") if posted else None,
        "failed": errors,
        "ok": bool(ok_channels),
    }


async def publish_pending_jobs(
    channel_username: str | None = None,
    delay_seconds: float | None = None,
    target_channels: Sequence[str] | None = None,
    language: str = "English",
    job_ids: Sequence[str] | None = None,
    republish: bool = False,
) -> dict[str, Any]:
    """
    Publish jobs sequentially. Default is every unpublished job.
    When `job_ids` is set, only those records are considered; already-published
    jobs are skipped unless `republish` is true.
    """
    try:
        targets = await resolve_publish_targets(
            list(target_channels or [])
            + ([channel_username] if channel_username else [])
        )
    except ValueError as exc:
        await broadcast_log("ERROR", str(exc), {"stage": "publish_pending"})
        return {"published": 0, "failed": 0, "skipped": 0, "errors": [str(exc)]}

    delay = settings.telegram_publish_delay if delay_seconds is None else float(delay_seconds)
    delay = max(0.0, delay)
    publish_language = normalize_publish_language(language)
    if job_ids:
        resolved = await get_jobs_by_ids(job_ids)
        pending = list(resolved.get("jobs") or [])
        if not republish:
            pending = [job for job in pending if not job.get("published_to_telegram")]
    else:
        pending = await get_unpublished_jobs()
    total = len(pending)
    published = 0
    failed = 0
    errors: list[str] = []
    label = ", ".join(targets)

    await broadcast_log(
        "PUBLISH_BATCH_STARTED",
        f"Publishing {total} pending job(s) to {len(targets)} channel(s) ({label}) "
        f"in {publish_language} (delay {delay:.1f}s between jobs)...",
        {
            "channels": targets,
            "pending": total,
            "delay_seconds": delay,
            "language": publish_language,
        },
    )

    for index, job in enumerate(pending, start=1):
        job_id = str(job.get("id") or job.get("message_id") or f"index-{index}")
        title = (job.get("title") or "").strip() or "Untitled Role"
        await broadcast_log(
            "PUBLISH_PROGRESS",
            f"Publishing {index}/{total}: [{title}] to {len(targets)} channel(s).",
            {
                "channels": targets,
                "current": index,
                "total": total,
                "job_id": job_id,
            },
        )
        try:
            already = bool(job.get("published_to_telegram"))
            result = await publish_job_to_channels(
                job,
                targets,
                language=publish_language,
                republish=republish and already,
            )
            if not result.get("ok"):
                raise RuntimeError("; ".join(result.get("failed") or ["no destination succeeded"]))
            marked = await mark_job_as_published(
                job_id,
                extra={
                    "published_url": result.get("posted_url"),
                    "published_message_id": result.get("posted_message_id"),
                    "published_channels": result.get("channels"),
                    "published_language": publish_language,
                },
            )
            if not marked:
                logger.warning("Published %s but failed to persist published flag", job_id)
            published += 1
        except Exception as exc:
            failed += 1
            detail = str(exc)
            errors.append(f"{job_id}: {detail}")
            logger.warning("Skipping publish of %s: %s", job_id, exc)
            await broadcast_log(
                "ERROR",
                f"Failed to publish [{title}] ({job_id}): {detail}",
                {"job_id": job_id, "channels": targets, "title": title},
            )

        if index < total and delay > 0:
            await asyncio.sleep(delay)

    await broadcast_log(
        "PUBLISH_BATCH_DONE",
        f"Publish batch finished for {label}: {published} posted, {failed} failed, "
        f"{total} attempted.",
        {
            "channels": targets,
            "published": published,
            "failed": failed,
            "attempted": total,
        },
    )
    return {
        "channels": targets,
        "channel": targets[0] if targets else None,
        "published": published,
        "failed": failed,
        "attempted": total,
        "errors": errors,
    }


async def _send_job_message(channel_username: str, body: str, *, max_wait: int = 90):
    """Send Markdown to the target channel; retry once on a short flood wait."""
    async with telegram_client() as client:
        entity = await client.get_entity(_entity_ref(channel_username))
        try:
            return await client.send_message(
                entity,
                body,
                parse_mode="md",
                link_preview=False,
            )
        except FloodWaitError as exc:
            wait_for = int(getattr(exc, "seconds", 0) or 0) + 1
            if wait_for > max_wait:
                raise RuntimeError(
                    f"Telegram flood wait of {wait_for}s exceeds {max_wait}s cap"
                ) from exc
            await broadcast_log(
                "PUBLISH_FLOOD_WAIT",
                f"Telegram rate limit: waiting {wait_for}s before retry...",
                {"seconds": wait_for, "channel": channel_username},
            )
            await asyncio.sleep(wait_for)
            return await client.send_message(
                entity,
                body,
                parse_mode="md",
                link_preview=False,
            )


async def _channel_live_snapshot(client: Any, handle: str) -> dict[str, Any]:
    """Resolve a destination handle and optional subscriber count."""
    snapshot: dict[str, Any] = {
        "handle": handle,
        "online": False,
        "subscribers": None,
        "title": None,
        "error": None,
    }
    try:
        entity = await client.get_entity(_entity_ref(handle))
        snapshot["online"] = True
        snapshot["title"] = getattr(entity, "title", None) or handle
        try:
            full = await client(GetFullChannelRequest(entity))
            full_chat = getattr(full, "full_chat", None)
            snapshot["subscribers"] = getattr(full_chat, "participants_count", None)
        except Exception as exc:
            snapshot["error"] = f"Insights restricted: {exc}"
    except Exception as exc:
        snapshot["error"] = str(exc)
    return snapshot


def _reaction_count(msg: Any) -> int:
    reactions = getattr(msg, "reactions", None)
    results = getattr(reactions, "results", None) if reactions is not None else None
    if not results:
        return 0
    return sum(int(getattr(item, "count", 0) or 0) for item in results)


async def get_telegram_session_status(*, force: bool = False) -> dict[str, Any]:
    """Lightweight session health for the navbar (cached briefly)."""
    now = monotonic()
    cached = _status_cache.get("value")
    if not force and cached and now - float(_status_cache.get("ts") or 0) < _STATUS_TTL_SECONDS:
        return cached

    saved = await get_admin_channels()
    defaults = [item["handle"] for item in saved if item.get("is_default") and item.get("handle")]
    all_handles = [item["handle"] for item in saved if item.get("handle")]
    target = defaults[0] if defaults else (all_handles[0] if all_handles else "")
    if not target:
        try:
            target = resolve_publish_channel(None)
        except ValueError:
            raw = (settings.telegram_target_channel or "").strip()
            target = raw if raw.startswith("@") or not raw else f"@{raw}"

    payload: dict[str, Any] = {
        "authorized": False,
        "connected": False,
        "target_channel": target,
        "target_channels": defaults or ([target] if target else []),
        "saved_channels": [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "handle": item.get("handle"),
                "is_default": bool(item.get("is_default")),
            }
            for item in saved
            if item.get("handle")
        ],
        "user": None,
        "error": None,
    }
    try:
        async with telegram_client() as client:
            payload["connected"] = True
            payload["authorized"] = bool(await client.is_user_authorized())
            if payload["authorized"]:
                me = await client.get_me()
                if me is not None:
                    username = getattr(me, "username", None)
                    payload["user"] = (
                        f"@{username}" if username else (me.first_name or "Telegram user")
                    )
    except Exception as exc:
        payload["error"] = str(exc)
        logger.info("Telegram session status: %s", exc)

    _status_cache["ts"] = now
    _status_cache["value"] = payload
    return payload


async def list_admin_channels(*, force: bool = False) -> list[dict[str, Any]]:
    """
    Channels where the logged-in account is creator or can post as admin.
    """
    now = monotonic()
    cached = _admin_channels_cache.get("value")
    if (
        not force
        and cached is not None
        and now - float(_admin_channels_cache.get("ts") or 0) < _ADMIN_CHANNELS_TTL_SECONDS
    ):
        return cached

    channels: list[dict[str, Any]] = []
    async with telegram_client() as client:
        async for dialog in client.iter_dialogs():
            if not getattr(dialog, "is_channel", False):
                continue
            entity = dialog.entity
            if not _can_post_to_channel(entity):
                continue
            username = getattr(entity, "username", None)
            handle = f"@{username}" if username else ""
            title = (
                dialog.name
                or getattr(entity, "title", None)
                or handle
                or str(dialog.id)
            )
            channels.append(
                {
                    "title": title,
                    "username": handle,
                    "id": int(dialog.id),
                }
            )

    channels.sort(key=lambda item: (item.get("title") or "").casefold())
    _admin_channels_cache["ts"] = now
    _admin_channels_cache["value"] = channels
    return channels


async def fetch_channel_stats(*, force: bool = False) -> dict[str, Any]:
    """
    Subscriber / views / reactions for TELEGRAM_TARGET_CHANNEL.
    Falls back to stored job counts plus null live metrics if Telegram restricts access.
    """
    now = monotonic()
    cached = _stats_cache.get("value")
    if not force and cached and now - float(_stats_cache.get("ts") or 0) < _STATS_TTL_SECONDS:
        return cached

    published_jobs = await get_published_jobs()
    unpublished = await get_unpublished_jobs()
    session = await get_telegram_session_status(force=force)
    saved = await get_admin_channels()

    try:
        target = resolve_publish_channel(None)
    except ValueError:
        target = session.get("target_channel") or ""

    channel_rows: list[dict[str, Any]] = [
        {
            "id": item.get("id"),
            "name": item.get("name"),
            "handle": item.get("handle"),
            "is_default": bool(item.get("is_default")),
            "online": False,
            "subscribers": None,
            "title": None,
            "error": None,
        }
        for item in saved
        if item.get("handle")
    ]

    payload: dict[str, Any] = {
        **session,
        "channel": target,
        "channel_title": None,
        "subscribers": None,
        "average_post_views": None,
        "engagement_count": None,
        "total_messages": None,
        "sampled_posts": 0,
        "total_jobs_published": len(published_jobs),
        "pending_jobs": len(unpublished),
        "source": "fallback",
        "fallback_reason": None,
        "channels": channel_rows,
    }

    if not target and not channel_rows:
        payload["fallback_reason"] = "No default destination channel is configured"
        _stats_cache["ts"] = now
        _stats_cache["value"] = payload
        return payload

    if not session.get("authorized"):
        reason = session.get("error") or "Telegram session is not authorized"
        payload["fallback_reason"] = reason
        for row in channel_rows:
            row["error"] = reason
        _stats_cache["ts"] = now
        _stats_cache["value"] = payload
        return payload

    try:
        async with telegram_client() as client:
            live_by_handle: dict[str, dict[str, Any]] = {}
            for row in channel_rows:
                handle = str(row.get("handle") or "")
                snap = await _channel_live_snapshot(client, handle)
                row.update(snap)
                live_by_handle[handle.casefold()] = snap

            primary = live_by_handle.get(str(target).casefold())
            if primary:
                payload["channel_title"] = primary.get("title") or target
                payload["subscribers"] = primary.get("subscribers")
                if primary.get("error") and primary.get("subscribers") is None:
                    payload["fallback_reason"] = primary.get("error")

            if target:
                entity = await client.get_entity(_entity_ref(target))
                views: list[int] = []
                reactions_total = 0
                sampled = 0
                last_id = None
                async for msg in client.iter_messages(entity, limit=40):
                    sampled += 1
                    if last_id is None:
                        last_id = getattr(msg, "id", None)
                    if getattr(msg, "views", None):
                        views.append(int(msg.views))
                    reactions_total += _reaction_count(msg)
                payload["sampled_posts"] = sampled
                if views:
                    payload["average_post_views"] = round(sum(views) / len(views))
                payload["engagement_count"] = reactions_total
                if payload["total_messages"] is None and last_id:
                    payload["total_messages"] = last_id
                if payload["subscribers"] is not None or views:
                    payload["source"] = "telegram"
                    payload["fallback_reason"] = None
            elif any(row.get("online") for row in channel_rows):
                payload["source"] = "telegram"
                payload["fallback_reason"] = None
    except Exception as exc:
        logger.warning("Channel stats fallback for %s: %s", target, exc)
        payload["fallback_reason"] = str(exc)
        await broadcast_log(
            "STATS_FALLBACK",
            f"Using fallback channel stats for {target}: {exc}",
            {"channel": target},
        )

    _stats_cache["ts"] = now
    _stats_cache["value"] = payload
    return payload


async def send_announcement(
    message: str,
    channel_username: str | None = None,
) -> dict[str, Any]:
    """Send a custom Markdown announcement to the target channel."""
    text = (message or "").strip()
    if not text:
        raise ValueError("Announcement text is required")
    target = resolve_publish_channel(channel_username)
    await broadcast_log(
        "BROADCAST_STARTED",
        f"Sending custom announcement to {target}...",
        {"channel": target, "chars": len(text)},
    )
    sent = await _send_job_message(target, text[:4000])
    posted_id = getattr(sent, "id", None)
    posted_url = _posted_message_url(target, posted_id)
    await broadcast_log(
        "BROADCAST_SENT",
        f"Announcement posted to {target}.",
        {"channel": target, "posted_message_id": posted_id, "posted_url": posted_url},
    )
    return {
        "channel": target,
        "posted_message_id": posted_id,
        "posted_url": posted_url,
    }
