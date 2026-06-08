# GroupPay — Project Context

Telegram Mini App (TMA) for splitting bills with PayNow QR codes. Python/Flask
backend + React/TypeScript frontend, served as one Flask app. Deployed as a
Docker container behind a named Cloudflare tunnel.

## Architecture

- **`bot.py`** — Flask app + Telegram bot (pyTelegramBotAPI, webhook mode). Serves
  the built React app, the JSON API, and the standalone PayNow pay page. Bot
  handlers: `/start`, `/split`, `/help`, `new_chat_members` (welcome), whisper-QR
  callback (`qr|…`), and the inline mark-paid callback (`resolve|…`).
- **`db.py`** — SQLite (`data/grouppay.db`). Tables: `sessions`, `participants`.
  Itemized breakdown stored as a JSON blob in `sessions.items_json`.
- **`ocr.py`** — receipt OCR. Primary: Gemini Flash vision API. Fallback:
  PaddleOCR + regex. Returns items, discounts, svc/GST, subtotal, grand total.
- **`paynow_qr.py`** — EMVCo/SGQR payload generator (PayNow phone + amount + ref).
- **`splitwize-spark/`** — React frontend (Vite). The whole UI is one big
  component: `src/pages/Index.tsx`. API client: `src/lib/api.ts`.

## Run / deploy

- **Production = Docker container.** `docker compose up --build -d` (needs sudo).
  Multi-stage build: Node stage builds the frontend → Python slim stage runs it,
  with `cloudflared` baked in. `entrypoint.sh` runs the tunnel (via `TUNNEL_TOKEN`)
  + `python bot.py`. Restart policy `unless-stopped`.
- **Dev = host bot.** `bash start.sh` (rebuilds frontend, starts named tunnel,
  runs `bot.py`). Use this for fast iteration. **Stop the container first** —
  both share the one tunnel/webhook, so only one bot can run at a time.
- **Env** (`.env`, gitignored): `BOT_TOKEN`, `GEMINI_API_KEY`, `WEBAPP_URL`,
  `TUNNEL_TOKEN`. Compose injects via `env_file`.
- **Persistence**: `./data` volume holds `grouppay.db`, `group_members.json`,
  `uploads/`. Paths resolve via `DATA_DIR` (relative to the script).
- **Cloudflare**: named tunnel `grouppay` → `bot.grouppay.uk` (domain
  `grouppay.uk` is on Cloudflare). Tunnel ID lives in `start.sh`; the secret
  token is in `.env` / `~/.cloudflared/`.

## Gotchas

- **One bot at a time** — host bot and container both register the same Telegram
  webhook on the same tunnel. Running both = duplicate/competing responses.
- **Cold Docker builds compile numpy from source** (~5 min): paddleocr 2.9.1 caps
  numpy `<2.0`, and numpy 1.26 has no cp313 wheel, so it builds with gcc. Warm
  cache skips it. Future fix: move base to `python:3.12-slim` (ships a 1.26 wheel).
- **Bot user ID** = numeric prefix of `BOT_TOKEN` (before `:`).
- **Flask `<int:>` route converter rejects negative numbers** — Telegram group
  chat IDs are negative, so member/chat routes take a string and cast to int.
- Run `black` before committing Python (repo convention).

## Charge math (itemized splits)

Ordered, matching how SG receipts compute:
`(subtotal − discount) → +service% → +GST%` (GST applies on subtotal+svc).
Charge components are rounded to cents **before** summing (so totals match the
printed receipt, no 1-cent drift). Service charge default 10%, GST default 9%,
both editable. Discount supports flat-$ or %.

Per-person: each person's raw share is summed across receipts, then rounded
**once** — non-payees ceil'd up so the collector never recovers less than owed;
the collector's amount is the remainder, clamped to ≥0.

## Multi-receipt model

One session can hold several bills ("Dinner", "Drinks", "Grab"). Each `Receipt`
owns its own items/discount/svc/GST/assignments — no cross-receipt leakage.
The flat `ocr*` state is the "draft" being edited; finished receipts snapshot
into `committedReceipts[]`. Same group across all receipts; someone gets $0 on a
bill they weren't part of. One whisper QR per person for their combined total.

## Terminology

- **Collector** = the person who fronted the bill and collects repayments
  (was "payee" — renamed because it read as jargon).

## What this session built (June 2026)

- **Dockerized** the app (multi-stage build, compose, volumes, cloudflared).
- **Named Cloudflare tunnel** → permanent `bot.grouppay.uk` (replaced dying
  random quick-tunnels); registered `grouppay.uk`.
- **Itemized splits**: discount detection + ordered discount→svc→GST math;
  svc/GST as editable rate toggles; receipt-accurate rounding.
- **Manual itemized entry** ("Enter Manually" → total-only OR itemize+assign).
- **Multi-receipt splits**: add/edit/remove multiple bills, per-bill names,
  combined review, per-bill breakdown in the View Split "Items" tab, bot message
  lists each bill.
- **Inline "Mark paid"** — collector taps to settle a person in the group chat
  (removed screenshot friction). Two-button whisper after QR unlock.
- **Camera capture** — "Take Photo" opens the device camera in the TMA.
- **Onboarding hints** — welcome on group-add + `/split` tip explaining the bot
  only sees @handles of people active after it joined.
- **Members via API** — fetched from backend by `chat_id`, not stuffed in the URL.
- **"Payee" → "Collector"** rename.
- **Reminder options** trimmed to 1h/3h/6h/12h/1d/3d/5d/7d (removed 1-min/5-min
  test intervals).
- Fixes: negative-chat-id member route, auto-reminder error loop on upgraded
  groups, rounding drift, svc-rate misread, duplicate-session guard + logging,
  Edit Split routing, multi-receipt edit-flow state leaks.
- **Security**: a live bot token was found hardcoded in git history (initial
  commit) and exposed on the public repo → token revoked + rotated. Gemini key
  and tunnel token were never committed. The dead token still sits in history
  (harmless post-revocation; scrub optional).

## Branches

- `main` — current, has everything above.
- `feat/multi-receipt`, `feat/docker-conversion` — merged into main (fast-forward).

## Known follow-ups / ideas (see DOCKER_NOTES.md too)

- Members who **leave** a group still linger in `group_members.json` (no
  `left_chat_member` cleanup beyond the existing handler — verify it works).
- `group_members.json` could move to SQLite (currently a JSON file).
- Mini App still has the screenshot/web-app payment path alongside the new
  inline mark-paid — could simplify later.
- True payment *verification* is impossible (P2P PayNow has no callback); the
  collector marking paid is the source of truth by design.
- No tap-to-pay deeplink into bank apps — PayNow has no public URI scheme; the
  download-QR-then-scan flow is the realistic option.
- Faster cold builds: switch base image to `python:3.12-slim`.
