# GroupPay

Telegram Mini App for splitting bills with friends. Generates real PayNow QR codes with pre-filled amounts, sends whisper-style private QR delivery in group chats, and tracks payment status.

## Architecture

```
┌─────────────────────────────────────────────┐
│  Telegram Group Chat                        │
│  ├─ /split command → opens Mini App         │
│  ├─ Bot announces split + amounts           │
│  └─ Whisper QR buttons (private per user)   │
└──────────────┬──────────────────────────────┘
               │
┌──────────────▼──────────────────────────────┐
│  bot.py (Python)                            │
│  ├─ Telegram Bot (pyTeleBot, long polling)  │
│  ├─ Flask API server (port 5000)            │
│  ├─ Static file server (serves React build) │
│  └─ PayNow QR generation (EMVCo format)     │
└──────────────┬──────────────────────────────┘
               │
┌──────────────▼──────────────┐  ┌────────────┐
│  db.py (SQLite)             │  │  React App  │
│  ├─ sessions table          │  │  (Vite)     │
│  └─ participants table      │  └────────────┘
└─────────────────────────────┘
```

## Files

| File | Description |
|------|-------------|
| `bot.py` | Main entry point — Telegram bot + Flask API + static server |
| `db.py` | SQLite helper — session/participant CRUD |
| `paynow_qr.py` | PayNow QR code generator (EMVCo TLV format with CRC-16) |
| `group_members.json` | Persisted group member tracking (auto-created) |
| `grouppay.db` | SQLite database (auto-created) |
| `splitwize-spark/` | React frontend (Telegram Mini App) |

## Prerequisites

- **Python 3.11+** — check with `python --version`
- **Node.js 18+** — check with `node --version` (for building frontend)
- **npm** — comes with Node.js
- **ngrok account** (free) — sign up at [ngrok.com](https://ngrok.com), then authenticate:
  ```bash
  ngrok config add-authtoken YOUR_AUTH_TOKEN
  ```
  Alternatively, use **Cloudflare Tunnel** (`cloudflared`) — no account needed for quick tunnels.
- **Telegram Bot token** — create one via [@BotFather](https://t.me/BotFather) on Telegram:
  1. Message `/newbot`
  2. Choose a name and username
  3. Copy the token (looks like `123456789:ABCdefGHIjklMNO...`)

## Setup (from scratch)

### 1. Clone the repo

```bash
git clone https://github.com/ctecte/groupPay.git
cd groupPay
```

### 2. Install Python dependencies

```bash
python -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Install frontend dependencies and build

```bash
cd splitwize-spark
npm install
npx vite build
cd ..
```

The built files go to `splitwize-spark/dist/` — Flask serves them automatically.

### 4. Set environment variables

```bash
export BOT_TOKEN="your-telegram-bot-token"
export WEBAPP_URL="https://your-domain.ngrok-free.dev"
```

Or edit the defaults directly in `bot.py` (lines 22-23).

### 5. Start an HTTPS tunnel

Telegram Mini Apps require HTTPS. Pick one:

#### Option A: ngrok (recommended)

```bash
# First time: authenticate (one-time setup)
ngrok config add-authtoken YOUR_NGROK_AUTH_TOKEN

# Free tier (random URL — changes each restart)
ngrok http 5000

# With a stable domain (free with ngrok account)
ngrok http 5000 --domain=your-subdomain.ngrok-free.dev
```

> **Important**: If using a random URL, you must update `WEBAPP_URL` every time ngrok restarts. A stable domain avoids this.

#### Option B: Cloudflare Tunnel

```bash
# Install: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
cloudflared tunnel --url http://localhost:5000
```

Copy the generated HTTPS URL and set it as `WEBAPP_URL`.

### 6. Configure the bot with BotFather

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. `/mybots` → select your bot → **Bot Settings** → **Group Privacy** → turn off`
3. Add the bot to a Telegram group chat

### 7. Run the bot

```bash
source venv/bin/activate
python bot.py
```

This starts:
- Telegram bot (long polling)
- Flask API on `http://0.0.0.0:5000`
- Static file server for the React build

### Quick start (all-in-one)

After initial setup, use the included script:

```bash
./start.sh
```

This kills any existing processes, rebuilds the frontend, and starts ngrok + bot.

### Auto-created files

These files are created automatically on first run (gitignored):

| File | Description |
|------|-------------|
| `grouppay.db` | SQLite database — sessions and participants |
| `group_members.json` | Cached Telegram group members (name + user ID) |
| `uploads/` | Uploaded payment screenshots |
| `bot.log` | Bot stdout/stderr |
| `ngrok.log` | ngrok stdout |

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/sessions` | Create a new split session |
| `GET` | `/api/sessions/<id>` | Get session details + participants |
| `PATCH` | `/api/sessions/<id>/participants/<name>/status` | Update payment status |
| `POST` | `/api/sessions/<id>/participants/<name>/screenshot` | Upload payment screenshot |
| `POST` | `/api/sessions/<id>/remind` | Send payment reminders to group |
| `GET` | `/api/sessions/<id>/qr/<name>` | Download PayNow QR code PNG |
| `GET` | `/api/sessions/<id>/pay/<name>` | Payment page with QR + details |

## How It Works

### Bill Splitting Flow

1. Someone types `/split` in a Telegram group
2. Bot sends a button that opens the Mini App
3. User enters: bill amount, GST/service charge, who paid, participants, split method
4. User confirms → app calls `POST /api/sessions`
5. Bot announces the split in the group with amounts per person
6. Each participant gets a **whisper button** — only they can reveal their PayNow QR

### Whisper QR Delivery

- Bot sends one message per participant with a "🔒 Show my QR code" button
- When clicked, bot checks the clicker's Telegram ID / username against the target
- ✅ Match → message updates with payee details + "Open PayNow QR" link
- ❌ No match → "🚫 This QR code is for X only" alert

### PayNow QR Codes

QR codes follow the **EMVCo Merchant-Presented QR Code** spec used by Singapore banks:
- Pre-filled with payee's phone number and exact amount
- Scannable by DBS, OCBC, UOB, GrabPay, PayLah, etc.
- Generated by `paynow_qr.py` with proper CRC-16/CCITT-FALSE checksum

### Member Tracking

- Bot tracks group members who have messaged or joined after it was added
- Stored in `group_members.json` (persists across restarts)
- Members appear as checkboxes when adding participants in the Mini App
- Telegram user IDs are used for proper mentions and whisper identity checks

## Development

### Frontend dev server (with hot reload)

```bash
cd splitwize-spark
npx vite --port 8080
```

Note: The Vite dev server has a proxy config for `/api` → `http://localhost:5000`, so the bot must be running alongside.

### Rebuild frontend after changes

```bash
cd splitwize-spark
npx vite build
```

No bot restart needed — Flask serves from `dist/` directly.

### Database

SQLite, auto-created on first run. To reset:

```bash
rm grouppay.db
# Bot recreates it on next start
```

## Tech Stack

- **Bot**: Python, [pyTelegramBotAPI](https://pypi.org/project/pyTelegramBotAPI/)
- **API**: Flask, flask-cors
- **Database**: SQLite
- **QR**: qrcode + Pillow, EMVCo PayNow format
- **Frontend**: React, TypeScript, Vite, Tailwind CSS
- **Tunnel**: ngrok / Cloudflare Tunnel
