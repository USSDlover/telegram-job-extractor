# System Architecture — Telegram Job Extractor

## Overview

Single-repository application that joins Telegram channels, discovers job categories via local LLM sampling, scrapes matching posts in the background, extracts structured job data with Ollama (Gemma 2), persists results to `jobs.json`, publishes selected jobs to destination Telegram channels stored in `channels.json`, and serves a React dashboard from FastAPI.

## System Diagram & Data Flow

```
┌─────────────────┐     Telethon (asyncio)      ┌──────────────────┐
│ Telegram Channel│ ───────────────────────────► │  scraper.py      │
│  (@channel)     │   sample / full history      │  TelegramClient  │
└─────────────────┘                              └────────┬─────────┘
                                                          │
                          message texts                   │
                                                          ▼
                                                 ┌──────────────────┐
                                                 │ ai_extractor.py  │
                                                 │ Ollama + gemma2  │
                                                 │ Pydantic schemas │
                                                 └────────┬─────────┘
                                                          │
                              broadcast_log(stage, …)     │
                                          │               │
                                          ▼               ▼
                                 ┌──────────────┐   ExtractedJob
                                 │ activity_log │         │
                                 │ SSE bus      │         ▼
                                 └──────┬───────┘  ┌──────────────┐
                                        │          │  storage.py  │
                                        │          │  jobs.json +  │
                                        │          │  channels.json│
                                        │          └──────┬───────┘
                                        │                 │
                                        │                 │  POST /api/jobs/.../publish
                                        │                 ▼
                                        │          ┌──────────────┐
                                        │          │ publisher.py │
                                        │          │ Telethon     │
                                        │          │ send_message │
                                        │          └──────────────┘
                                        │                 │
          GET /api/stream-logs          │    GET /api/jobs│/categories
                                        ▼                 ▼
┌─────────────────┐     Static mount /           ┌──────────────────┐
│    main.py      │ ───────────────────────────► │ React UI         │
│    FastAPI      │   frontend/dist              │ ControlPanel +   │
│                 │ ◄─────────────────────────── │ ActivityConsole +│
└─────────────────┘   POST /api/discover|extract │ JobDashboard     │
```

### Flow Summary

1. User adds one or more channel usernames in **ControlPanel** (SSE client already connected to `/api/stream-logs`). Activity Console sits in a sticky top-right rail on desktop.
2. `POST /api/discover` → Telethon samples each channel → aggregated texts → Gemma 2 returns unified `CategoryDiscoveryResult`; stages stream live to **ActivityConsole**.
3. User selects categories/titles → `POST /api/extract` starts a FastAPI `BackgroundTasks` job across all channels sequentially; progress events continue over SSE.
4. Scraper iterates each channel’s messages → extracts URLs from text, Telethon entities, and webpage previews → optionally deep-scrapes job-board pages → Gemma 2 extracts `ExtractedJob` → filter by selection → append to `jobs.json` (external apply URL preferred; `telegram_url` as fallback metadata; `published_to_telegram` defaults to `false`).
5. **JobDashboard** auto-refreshes on `JOB_SAVED` / `JOB_PUBLISHED` using `GET /api/jobs`. Cards show Pending/Published badges, per-job **Publish to Telegram**, a **Republish** action in the card action row (with `publication_history`) for already-posted jobs, and a global **Publish All Unposted** action. The navbar lists every destination from `GET /api/channels` as live status pills, with subscriber tooltips from `GET /api/telegram/stats`.
6. Navbar tabs switch between **Extractor Dashboard** and **Telegram Channel Hub**. Hub manages destination channels (`GET`/`POST`/`DELETE`/`PATCH /api/channels`), loads `GET /api/telegram/stats`, lists published jobs, and can send a custom Markdown broadcast via `POST /api/telegram/broadcast`.

## API Specification

### `POST /api/discover`

Join/access one or more channels and discover a unified category/title set from aggregated samples.

**Request body**

```json
{
  "channels": ["@tech_jobs", "@frontend_jobs", "@remote_work"]
}
```

Legacy single-channel `channel` is still accepted and merged into `channels`.

**Response `200`**

```json
{
  "channels": ["@tech_jobs", "@frontend_jobs", "@remote_work"],
  "discovered_categories": ["Engineering", "Hospitality"],
  "sample_count": 54,
  "per_channel": { "@tech_jobs": 20, "@frontend_jobs": 18, "@remote_work": 16 },
  "errors": []
}
```

**Errors**: `400` invalid/empty channels; `502` Telegram/Ollama failure.

---

### `POST /api/extract`

Trigger background scrape + AI extraction across all configured channels.

**Request body**

```json
{
  "channels": ["@tech_jobs", "@frontend_jobs"],
  "selected_categories": ["Engineering"]
}
```

**Response `202`**

```json
{
  "status": "started",
  "channels": ["@tech_jobs", "@frontend_jobs"],
  "message": "Extraction pipeline started for 2 channel(s)"
}
```

`selected_titles` is accepted for backward compatibility but ignored by the Control Board (categories-only filtering).

---

### `GET /api/jobs`

List stored jobs with optional category filter and sort order.

**Query parameters**

| Param      | Type   | Required | Description |
|------------|--------|----------|-------------|
| `category` | string | no       | Exact category filter |
| `sort_by`  | string | no       | `date_desc` (default), `date_asc`, `category_asc`, `title_asc` |

**Response `200`**

```json
{
  "jobs": [
    {
      "id": "channel_msg_12345",
      "channel": "@tech_jobs_channel",
      "message_id": 12345,
      "date": "2026-07-20T14:30:00+00:00",
      "is_job_posting": true,
      "title": "Frontend Developer",
      "category": "Engineering",
      "company": "Acme Corp",
      "apply_links": ["https://boards.greenhouse.io/acme/jobs/1"],
      "telegram_url": "https://t.me/tech_jobs_channel/12345",
      "translated_summary": "React/TypeScript role, remote-friendly, 3+ years experience.",
      "original_language": "English",
      "published_to_telegram": false,
      "publication_history": []
    }
  ],
  "total": 1
}
```

---

### `DELETE /api/jobs/clear-all`

Atomically wipe `jobs.json` to `[]` (registered before `/{job_id}` so the path is not captured as an id).

**Response `200`**

```json
{
  "success": true,
  "message": "All jobs cleared successfully",
  "removed": 42
}
```

---

### `POST /api/jobs/bulk-delete`

Delete multiple jobs by `id` (or `message_id`) in one locked write.

**Request body**

```json
{ "job_ids": ["channel_123", "channel_456"] }
```

**Response `200`**

```json
{
  "success": true,
  "deleted_count": 2,
  "deleted_ids": ["channel_123", "channel_456"],
  "not_found": [],
  "message": "Deleted 2 job(s)"
}
```

---

### `DELETE /api/jobs/{job_id}`

Delete a single job by unique `id` or `message_id`.

**Response `200`**: `{ "success": true, "deleted_id": "..." }` — **404** if missing.

---

### `POST /api/jobs/{job_id}/publish`

Publish one stored job to the destination channel using the existing Telethon session.

**Request body** (optional)

```json
{
  "target_channels": ["@huntjobarmenia", "@expatjobs_am"],
  "language": "Persian",
  "republish": false
}
```

`language` is `English` (default), `Persian`, or `Arabic`. Non-English posts are translated with local Gemma 2 before Telegram formatting. Falls back to channels marked `is_default: true` in `channels.json` when the list is empty. `TELEGRAM_TARGET_CHANNEL` is a last-resort seed/fallback. Legacy `target_channel` / query `channel_username` are still accepted. Posts each destination sequentially with `TELEGRAM_CHANNEL_DELAY` (default 1.5s).

If the job already has `published_to_telegram == true`, the request is rejected unless `republish` is `true`. A successful send (first publish or republish) appends one `publication_history` row per destination:

```json
{ "channel": "@huntjobarmenia", "language": "English", "published_at": "2026-09-03T00:10:00+00:00" }
```

**Response `200`**

```json
{
  "status": "published",
  "job_id": "job_am_53227",
  "channels": ["@huntjobarmenia", "@expatjobs_am"],
  "posted_url": "https://t.me/huntjobarmenia/42",
  "published_to_telegram": true,
  "publication_history": [
    { "channel": "@huntjobarmenia", "language": "Persian", "published_at": "2026-09-03T00:10:00+00:00" }
  ],
  "republish": false,
  "message": "Published job job_am_53227 to 2 channels (@huntjobarmenia, @expatjobs_am) in Persian"
}
```

`status` is `republished` when `republish` was true. **404** if the job is missing; **400** if no target channel is configured or the job is already published without `republish`; **502** if every destination fails.

---

### `POST /api/jobs/publish-all-pending`

Queue a background loop over every job with `published_to_telegram == false`. Posts sequentially with `TELEGRAM_PUBLISH_DELAY` (default 2.5s) between messages. A single failure is logged over SSE (`ERROR`) and the loop continues.

**Request body** (optional)

```json
{ "target_channels": ["@huntjobarmenia", "@expatjobs_am"], "language": "Arabic", "delay_seconds": 2.5 }
```

Falls back to `is_default` channels in `channels.json` when the list is empty. Query `channel_username` / `delay_seconds` remain as aliases. Every pending job is translated to `language` before posting.

**Response `202`-style `200`**

```json
{
  "status": "started",
  "channel": "@huntjobarmenia",
  "pending": 12,
  "delay_seconds": 2.5,
  "message": "Publishing 12 job(s) to @huntjobarmenia"
}
```

`status: idle` when there is nothing to publish.

---

### `GET /api/channels`

List destination admin channels persisted in `channels.json`.

**Response `200`**

```json
{
  "channels": [
    { "id": "c1", "name": "Main Channel", "handle": "@huntjobarmenia", "is_default": true, "default_language": "English" }
  ],
  "total": 1
}
```

---

### `POST /api/channels`

Add a destination channel. Handle is trimmed and prefixed with `@`.

**Request body**

```json
{ "name": "Main Channel", "handle": "huntjobarmenia", "default_language": "Persian" }
```

**Response `200`**: `{ "channel": {…}, "channels": […], "total": n }` — **400** if name/handle is missing or the handle already exists.

---

### `DELETE /api/channels/{channel_id}`

Remove a saved destination channel.

**Response `200`**: `{ "success": true, "deleted_id": "c1" }` — **404** if missing.

---

### `PATCH /api/channels/{channel_id}/language`

Set a channel's default publish language (`English` | `Persian` | `Arabic`). The publish modal pre-selects this radio when the channel is checked.

---

### `PATCH /api/channels/{channel_id}/default`

Toggle `is_default` on a saved destination channel. Multiple defaults are allowed; publish APIs fan out to every default when `target_channels` is omitted.

**Response `200`**: `{ "channel": {…}, "channels": […], "total": n }` — **404** if missing.

---

### `GET /api/telegram/admin-channels`

Legacy Telethon discovery: lists Telegram channels where the logged-in session is creator or has `post_messages` admin rights (`iter_dialogs()`). Publish targets now come from `GET /api/channels`.

**Response `200`**

```json
{
  "channels": [
    { "title": "Hunt Job in Armenia EN", "username": "@huntjobarmenia", "id": -1001234567890 }
  ],
  "total": 1
}
```

---

### `GET /api/telegram/status`

Navbar health check for the Telethon session plus saved destination handles.

**Response `200`**

```json
{
  "authorized": true,
  "connected": true,
  "target_channel": "@huntjobarmenia",
  "target_channels": ["@huntjobarmenia"],
  "saved_channels": [
    { "id": "c1", "name": "Main Channel", "handle": "@huntjobarmenia", "is_default": true }
  ],
  "user": "@youraccount",
  "error": null
}
```

---

### `GET /api/telegram/stats`

Channel analytics via `GetFullChannelRequest` plus a sample of recent post views/reactions. Falls back to stored job counts when Telegram restricts insights.

**Response `200`**

```json
{
  "channel": "@huntjobarmenia",
  "subscribers": 1200,
  "average_post_views": 340,
  "engagement_count": 28,
  "total_jobs_published": 12,
  "pending_jobs": 4,
  "source": "telegram",
  "recent_published": [],
  "channels": [
    {
      "id": "c1",
      "handle": "@huntjobarmenia",
      "name": "Main Channel",
      "online": true,
      "subscribers": 1200,
      "error": null
    }
  ]
}
```

`source` is `fallback` when live metrics are unavailable. `channels` is one row per saved destination (reachability + subscriber count). Message sampling still runs only on the primary default channel.

---

### `POST /api/telegram/broadcast`

Send a custom Markdown announcement to the target channel.

**Request body**

```json
{ "message": "*Hiring this week*\\nNew roles are live." }
```

---

### `GET /api/categories`

Distinct non-empty categories present in `jobs.json`.

**Response `200`**

```json
{
  "categories": ["Engineering", "Hospitality"]
}
```

---

### `GET /api/stream-logs`

Server-Sent Events (SSE) activity stream. Clients connect with `EventSource`.

**Event payload**

```json
{
  "stage": "EXTRACTION_PROGRESS",
  "message": "Processing post 4/100 through Ollama...",
  "data": { "current": 4, "total": 100 },
  "ts": "2026-07-23T17:00:00+00:00"
}
```

Common stages: `JOINING_TELEGRAM`, `FETCHING_POSTS`, `CALLING_OLLAMA`, `DISCOVERED_CATEGORIES`, `EXTRACTION_PROGRESS`, `JOB_SAVED`, `EXTRACTION_DONE`, `TRANSLATING`, `TRANSLATION_DONE`, `PUBLISH_STARTED`, `REPUBLISH_STARTED`, `JOB_PUBLISHED`, `PUBLISH_BATCH_STARTED`, `PUBLISH_BATCH_DONE`, `ERROR`.

---

### Static UI

In production, FastAPI mounts `frontend/dist` at `/` after API routes. SPA fallback serves `index.html` for non-API paths.

## Local AI Structured Output Schemas

Schemas are Pydantic v2 models; Ollama is instructed to return JSON matching `model_json_schema()`.

### `ExtractedJob`

| Field                | Type           | Description |
|----------------------|----------------|-------------|
| `is_job_posting`     | `bool`         | Whether the message is a job posting |
| `title`              | `str`          | Normalized English title |
| `category`           | `str`          | Job category classification |
| `company`            | `Optional[str]`| Employer name if present |
| `apply_links`        | `List[str]`    | External apply / contact URLs |
| `translated_summary` | `str`          | Concise English summary of requirements |
| `original_language`  | `Optional[str]`| Source language when not English |

**Persistence rule**: Persist when `is_job_posting` is `true` and the job matches selected category filters (Telegram URL is injected when no external apply link exists). Each stored record also has `published_to_telegram` (default `false`) and `publication_history` (default `[]`). Re-extraction preserves `true` and existing history so already-posted jobs are not unmarked.

### `CategoryDiscoveryResult`

| Field                   | Type       | Description |
|-------------------------|------------|-------------|
| `discovered_categories` | `List[str]`| Distinct English job categories in samples |

## Configuration

| Variable                   | Purpose                          | Default                |
|----------------------------|----------------------------------|------------------------|
| `TELEGRAM_API_ID`          | Telethon API ID                  | (required)             |
| `TELEGRAM_API_HASH`        | Telethon API hash                | (required)             |
| `TELEGRAM_SESSION`         | Session file basename            | `telegram_job_session` |
| `TELEGRAM_TARGET_CHANNEL`  | Seed/fallback destination if `channels.json` is empty | (empty; e.g. `@huntjobarmenia`) |
| `CHANNELS_FILE`            | Path to destination-channel JSON store | `../channels.json` |
| `TELEGRAM_PUBLISH_DELAY`   | Seconds between batch posts      | `2.5`                  |
| `TELEGRAM_CHANNEL_DELAY`   | Seconds between destination channels | `1.5`              |
| `OLLAMA_HOST`              | Ollama base URL                  | `http://127.0.0.1:11434` |
| `OLLAMA_MODEL`             | Model name                       | `gemma2`               |
| `JOBS_FILE`                | Path to JSON store               | `../jobs.json`         |
| `SCRAPE_LIMIT`             | Max messages per extract run     | `100`                  |

## Concurrency & Safety

- `storage.py` uses separate `asyncio.Lock`s around read-modify-write of `jobs.json` and `channels.json`.
- Background extraction never raises into the request handler; per-message AI/Telegram errors are logged and skipped.
- Deduplication key: `{channel}_{message_id}`.
- Batch publish never aborts the loop on a single send failure; flood waits under 90s are retried once. The Telethon user must be an admin of each destination channel.
