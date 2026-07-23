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

## Architectural Decisions

1. **Telethon + FastAPI background tasks** — Channel sampling and full extraction run as async Telethon operations; extraction is kicked off via FastAPI `BackgroundTasks` so the HTTP response returns immediately (`202`).
2. **Local Gemma 2 via Ollama** — No cloud LLM dependency. Structured outputs enforced by embedding Pydantic `model_json_schema()` in the prompt and validating the parsed JSON response.
3. **JSON file persistence** — `jobs.json` at repo root keeps the MVP simple; `asyncio.Lock` + atomic write prevents corruption under concurrent appends.
4. **Static Vite build mounted in FastAPI** — Single process serves API and UI in production; Vite dev server proxies `/api` during local frontend development.
5. **Filter at extract time** — User-selected categories/titles gate what gets persisted, reducing noise in the dashboard.
6. **SSE activity bus** — `broadcast_log(stage, message, data)` fans out structured events to all `EventSource` clients via asyncio queues + a short ring buffer.
7. **UI state sync via events** — `DISCOVERED_CATEGORIES` updates Control Board checkboxes immediately; `JOB_SAVED` debounced-triggers Job Feed + category dropdown refresh.
8. **Multi-channel aggregation** — Discovery samples each configured channel, concatenates texts, then runs Gemma 2. Extraction walks channels sequentially on one Telethon session.
9. **Top-right activity rail** — Desktop layout uses `grid-template-columns: 1fr 380px` with a sticky Activity Console.
10. **Sort controls** — `GET /api/jobs?sort_by=` supports `date_desc`, `date_asc`, `category_asc`, `title_asc`.
11. **Chunked discovery + debug samples** — Large multi-channel sample sets are split by post count / character budget (`DISCOVERY_CHUNK_POSTS`, `DISCOVERY_CHUNK_CHARS`), merged across chunks, and every prompt/raw response is appended to `debug_samples.json` for inspection via `GET /api/debug/samples`.
12. **Date filtering** — `GET /api/jobs` accepts `preset` (`today`, `this_week`, `this_month`, `all_time`, `custom`) plus optional `start_date` / `end_date` (`YYYY-MM-DD`). Job Feed pills trigger re-fetch automatically.
13. **Link metadata enrichment** — Short/link-only Telegram posts are enriched via `link_preview.py` (`httpx` + BeautifulSoup) using Open Graph / meta / headings before Gemma 2 runs. Failures never abort the pipeline; successes stream as `LINK_SCRAPER` SSE events and are stored under `enriched_metadata` in `debug_samples.json`.
14. **Date-bounded Telethon scrape** — `POST /api/extract` accepts `date_preset` / custom dates; the scraper walks newest-first and **breaks** once `msg.date < start_date`, avoiding thousands of historical posts. Category labels are normalized (trim punctuation, case-insensitive dedupe) for both discovery UI and `GET /api/categories`.
15. **Cooperative cancellation** — A shared `asyncio.Event` (`stop_scraper_event`) is cleared on each extract start and set by `POST /api/stop-extraction`. The Telethon loop checks the flag before each message / Ollama call and emits `EXTRACTION_STOP_REQUESTED` / `EXTRACTION_STOPPED` SSE events; the Control Board shows a red **Stop Extraction** button while running.
16. **Strict multilingual English output** — Gemma prompts require detecting Armenian/Russian (and mixed) source text, translating titles/summaries to professional English, and never emitting Armenian script or Cyrillic in `title`, `category`, or `translated_summary` (optional `original_language` field retained).

## Future Work

- [ ] Migrate `jobs.json` → SQLite (or Postgres) with proper indexes
- [ ] Concurrent multi-channel extraction with bounded worker pool
- [ ] Pagination / infinite scroll on `GET /api/jobs`
- [ ] Session login helper UI for first-time Telethon auth
- [ ] Retry / backoff policies for Ollama and Telegram rate limits
- [ ] Export jobs as CSV
- [ ] Persist channel list across sessions (localStorage / backend config)
- [ ] Optional UI panel to browse `debug_samples.json` without curling the API
