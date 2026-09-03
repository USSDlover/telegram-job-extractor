# Progress Log — Telegram Job Extractor

## Features & Milestones

| Feature | Status | Notes |
|---------|--------|-------|
| Repo layout (`backend/`, `frontend/`, `docs/`) | Done | Vite React + FastAPI monorepo |
| Config via `.env` | Done | Telethon + Ollama settings |
| Async `jobs.json` storage with lock | Done | Dedup by channel + message_id |
| Ollama Gemma 2 structured extraction | Done | Pydantic JSON schema prompting |
| Category discovery from samples | Done | Multi-channel + chunked discovery |
| Discovery debug samples | Done | `debug_samples.json` + `GET /api/debug/samples` |
| Background scrape + extract pipeline | Done | Sequential multi-channel extraction |
| Jobs list + category filter API | Done | `GET /api/jobs`, `GET /api/categories` |
| Date range filtering | Done | Presets + custom `start_date`/`end_date` |
| Job sort controls | Done | `sort_by` API + Sort By dropdown |
| ControlPanel UI | Done | Multi-channel chips + discovery/extract |
| JobDashboard UI | Done | Filter, sort, date presets, refresh |
| FastAPI serves Vite `dist` | Done | Mount + SPA fallback |
| System / progress docs | Done | `docs/SYSTEM.md`, this file |
| SSE live activity stream | Done | `GET /api/stream-logs` + `activity_log.py` |
| Stage logs in Telethon / Ollama pipelines | Done | JOINING → FETCHING → OLLAMA → JOB_SAVED |
| ActivityConsole UI | Done | Sticky top-right rail on desktop |
| Dynamic feed / category sync | Done | Auto-refresh on `JOB_SAVED` |
| Multi-channel support | Done | `channels: List[str]` on discover/extract |
| Link metadata enrichment | Done | OG/meta scrape for short/link-only posts |
| Date-bounded scraping | Done | Early Telethon exit past `start_date` |
| Category checklist UX | Done | Scroll, search, select-all, dedupe |
| Extraction stop / cancel | Done | `POST /api/stop-extraction` + asyncio.Event |
| HY/RU → English translation | Done | Strict multilingual Gemma prompts |
| Job deletion | Done | `DELETE /api/jobs/{id}` + feed Delete button |
| Simplified control board | Done | Discover → select → extract (restored) |
| Telegram link fallback | Done | `https://t.me/channel/msg_id` when no apply URL |
| Category sampling restored | Done | `sample_channel_categories` + Join & Discover UI |
| Telethon entity / webpage URL extraction | Done | `MessageEntityTextUrl` + `media.webpage.url` |
| Deep job-board page scrape | Done | `job.am` / `staff.am` / etc. body text → Gemma |
| Bulk clear all jobs | Done | `DELETE /api/jobs/clear-all` + Clear All Jobs UI |
| Chunk channel attribution | Done | Per-chunk `@channel` tags in SSE + debug_samples |
| Suggested titles removed | Done | Categories-only discovery UI + schema |
| HY/RU short-title prompts | Done | Translate-first + short title+link = valid |
| Prompt noise sanitization | Done | Clean Post N + URL Slug lines for Gemma |
| URL slug category fallback | Done | Deterministic slug map when LLM returns 0 |
| Bulk select / delete jobs | Done | Checkboxes + `POST /api/jobs/bulk-delete` |
| Telegram job publisher | Done | `publisher.py` Markdown posts to `TELEGRAM_TARGET_CHANNEL` |
| Publish state flag | Done | `published_to_telegram` on each `jobs.json` record |
| Publish APIs | Done | `POST /api/jobs/{id}/publish` + `publish-all-pending` |
| Navbar + Channel Hub UI | Done | Extractor / Hub tabs, session badge, analytics widgets |
| Publish actions in Job Feed | Done | Pending/Published badges + publish one / publish all |
| Channel stats + broadcast API | Done | `GET /api/telegram/stats`, `POST /api/telegram/broadcast` |
| Publish channel picker | Done | Modal + `target_channel` JSON body + localStorage recents |
| Multi-channel publish | Done | Admin-channel checkboxes + `target_channels` fan-out |
| Destination channel CRUD | Done | `channels.json` + `/api/channels` + ChannelManager UI |
| Publish language translation | Done | English / Persian / Arabic via Gemma 2 + modal radios |
| Job republish | Done | `republish` flag bypasses already-published skip; `publication_history` + JobCard Republish action |
| Multi-channel header status | Done | Navbar pills from `/api/channels` + live/subscriber tooltips from `/api/telegram/stats` |
| Publish selected jobs | Done | Toolbar action + `POST /api/jobs/publish-selected` with `job_ids` and `republish` |

## Architectural Decisions

1. **Telethon + FastAPI background tasks** — Channel sampling and full extraction run as async Telethon operations; extraction is kicked off via FastAPI `BackgroundTasks` so the HTTP response returns immediately (`202`).
2. **Local Gemma 2 via Ollama** — No cloud LLM dependency. Structured outputs enforced by embedding Pydantic `model_json_schema()` in the prompt and validating the parsed JSON response.
3. **JSON file persistence** — `jobs.json` at repo root keeps the MVP simple; `asyncio.Lock` + atomic write prevents corruption under concurrent appends.
4. **Static Vite build mounted in FastAPI** — Single process serves API and UI in production; Vite dev server proxies `/api` during local frontend development.
5. **Filter at extract time** — User-selected categories/titles gate what gets persisted, reducing noise in the dashboard.
6. **SSE activity bus** — `broadcast_log(stage, message, data)` fans out structured events to all `EventSource` clients via asyncio queues + a short ring buffer.
7. **UI state sync via events** — `JOB_SAVED` debounced-triggers Job Feed + category dropdown refresh.
8. **Multi-channel aggregation** — Extraction walks channels sequentially on one Telethon session.
9. **Top-right activity rail** — Desktop layout uses `grid-template-columns: 1fr 380px` with a sticky Activity Console.
10. **Sort controls** — `GET /api/jobs?sort_by=` supports `date_desc`, `date_asc`, `category_asc`, `title_asc`.
11. **Chunked discovery + debug samples** — Discovery APIs remain available for debugging; the Control Board no longer drives category discovery UI.
12. **Date filtering** — `GET /api/jobs` accepts `preset` plus optional `start_date` / `end_date`.
13. **Link metadata enrichment** — Short/link-only posts are enriched via Open Graph metadata before Gemma 2.
14. **Date-bounded Telethon scrape** — Newest-first iteration breaks once `msg.date < start_date`.
15. **Cooperative cancellation** — Shared `asyncio.Event` with Stop Extraction UI.
16. **Strict multilingual English output** — Armenian/Russian source text translated to English fields; optional `original_language`.
17. **Telegram apply fallback** — Every saved job gets at least one HTTP link: extracted external apply URLs when present, otherwise `https://t.me/{channel}/{message_id}`. Feed buttons label Telegram URLs as **View Telegram Post**.
18. **Category sampling restored** — Control Board step 1 runs `POST /api/discover` via `sample_channel_categories()` (Telethon samples + link enrichment + Gemma 2). Discovered categories merge with stored `GET /api/categories` results; Ollama failures surface as HTTP 400/502 instead of empty silent lists. Steps 2–3 retain selection, date-bounded extract, stop, deletion, and Telegram fallbacks.
19. **Hidden link extraction** — Apply URLs are taken from plain text, Telethon `entities` (`MessageEntityTextUrl` / `MessageEntityUrl`), and `message.media.webpage.url`. External destinations (e.g. `job.am`) become primary `apply_links`; `telegram_url` stays as metadata / last-resort fallback.
20. **Deep link preview scrape** — For known Armenian/regional job boards, `link_preview.py` fetches OG metadata plus main page body text and appends it to the Gemma payload.
21. **Bulk job erasure** — `DELETE /api/jobs/clear-all` atomically writes `[]` under the storage lock; Job Feed exposes a confirmed **Clear All Jobs** control.
22. **Chunk-level discovery diagnostics** — Discovery samples are attributed per channel (`{channel, text}`). Each Ollama chunk logs `Chunk X/Y (@ch1, @ch2 - N posts)…`, persists a `debug_samples.json` snapshot (`channels_in_chunk`, prompt, raw AI response, extracted labels), and the Activity Console renders `@handle` chips plus an **Inspect** expander for empty/failed chunks.
23. **Categories-only discovery** — `suggested_titles` removed from Pydantic discovery schema, API payloads, and Control Board UI; extraction filters by selected categories only.
24. **Translate-first short HY/RU posts** — Discovery and extraction prompts require Armenian/Russian → English before classification, treat 1–5 word title+link posts as valid jobs, and force link-preview enrichment for short non-English captions with URLs.
25. **Sanitized discovery prompts + slug fallback** — Noisy `[Telegram Message]` / link-metadata blocks are stripped into `Post N: title (URL Slug: …)` lines. `extract_keywords_from_urls()` maps job.am-style slugs (e.g. `vacharqi-menejer` → Sales) and a **Fallback Engine** fills categories when Gemma returns empty.
26. **Bulk job selection** — Job Feed checkboxes + Select All toolbar call `POST /api/jobs/bulk-delete` with confirmed multi-id removal under the storage lock.
27. **Outbound Telegram publisher** — `publisher.py` reuses the Telethon session to post Markdown job cards to `TELEGRAM_TARGET_CHANNEL`. `published_to_telegram` is stored on each job, set atomically after a successful send, and preserved across re-extraction. Batch publish walks unpublished jobs sequentially with a configurable delay; one failed post is SSE-logged and skipped.
28. **Channel Hub UI** — Sticky navbar tabs switch Extractor vs Telegram Hub. Hub widgets read `GetFullChannelRequest` (with fallback), list published jobs, and send manual Markdown broadcasts. Job cards expose publish status and actions; `JOB_PUBLISHED` refreshes both views via the SSE bus.
29. **Publish channel picker** — `ChannelSelectModal` asks for destinations before single or bulk publish. It portals to `document.body`, loads `GET /api/channels` on open, default-checks `is_default` rows, and posts `{ "target_channels": ["@handle"] }`.
30. **Multi-destination publish** — Publish APIs accept `target_channels` and post sequentially with a 1.5s inter-channel delay. An empty list falls back to `is_default` rows in `channels.json`.
31. **Persisted destination channels** — Admin destinations live in `channels.json` with locked CRUD helpers (`get_admin_channels`, `add_admin_channel`, `delete_admin_channel`, `toggle_default_channel`). Telegram Hub exposes a Manage Channels table. `TELEGRAM_TARGET_CHANNEL` only seeds an empty store.
32. **Publish-time translation** — `translate_job_summary` / `localize_job_for_publish` run Gemma 2 when `language` is Persian or Arabic. Telegram cards get language badges, translated labels, and RTL marks. Channel records store `default_language` so the picker radio pre-selects.
33. **Republish already-posted jobs** — `POST /api/jobs/{id}/publish` rejects jobs with `published_to_telegram == true` unless `republish: true`. Each successful send appends `{channel, language, published_at}` rows to `publication_history` (preserved on re-extraction). Shared `JobCard` always renders a visible **🔄 Republish** button (`btn-republish`) on published cards in both the Extractor feed and Telegram Hub. `onOpenPublishModal(job, { republish: true })` opens `ChannelSelectModal` and posts `{ republish: true, target_channels, language }`. SSE uses `REPUBLISH_STARTED` plus the existing `TRANSLATING` / `JOB_PUBLISHED` stages.
34. **Multi-channel navbar status** — The header no longer shows a single static target handle. It loads every destination from `GET /api/channels` and paints wrapping live/offline pills. `GET /api/telegram/stats` now returns a `channels[]` snapshot (reachable + subscriber count) used for hover tooltips.
35. **Publish Selected** — Job Feed checkboxes drive `POST /api/jobs/publish-selected` with `{ job_ids, target_channels, language, republish }`. If any checked card is already published, the picker opens in republish mode so those jobs are re-sent instead of skipped.

## Future Work

- [ ] Migrate `jobs.json` → SQLite (or Postgres) with proper indexes
- [ ] Concurrent multi-channel extraction with bounded worker pool
- [ ] Pagination / infinite scroll on `GET /api/jobs`
- [ ] Session login helper UI for first-time Telethon auth
- [ ] Retry / backoff policies for Ollama and Telegram rate limits
- [ ] Export jobs as CSV
- [x] Persist destination channel list in `channels.json` with Hub CRUD
- [ ] Optional UI panel to browse `debug_samples.json` without curling the API
