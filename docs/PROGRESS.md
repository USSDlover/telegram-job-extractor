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

## Future Work

- [ ] Migrate `jobs.json` → SQLite (or Postgres) with proper indexes
- [ ] Concurrent multi-channel extraction with bounded worker pool
- [ ] Pagination / infinite scroll on `GET /api/jobs`
- [ ] Session login helper UI for first-time Telethon auth
- [ ] Retry / backoff policies for Ollama and Telegram rate limits
- [ ] Export jobs as CSV
- [ ] Persist channel list across sessions (localStorage / backend config)
- [ ] Optional UI panel to browse `debug_samples.json` without curling the API
