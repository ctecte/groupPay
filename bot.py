#!/usr/bin/env python3
"""
GroupPay Telegram Bot — opens the React app as a Telegram Mini App.
Tracks group members and passes them to the webapp.
Includes Flask API server for session management.
"""

import telebot
from telebot import types
import os
import io
import json
import threading
from urllib.parse import quote

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import qrcode

import db
from paynow_qr import generate_paynow_qr_data

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8061320633:AAEFegJpAs281zT4ySk20z2o_SHzh9tg3Rw")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://subfossorial-ritualistically-christene.ngrok-free.dev")
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

MEMBERS_FILE = os.path.join(os.path.dirname(__file__), "group_members.json")


def _load_members() -> dict[int, dict[int, str]]:
    if os.path.exists(MEMBERS_FILE):
        try:
            with open(MEMBERS_FILE) as f:
                raw = json.load(f)
            # JSON keys are strings, convert back to ints
            return {int(cid): {int(uid): name for uid, name in members.items()}
                    for cid, members in raw.items()}
        except Exception:
            pass
    return {}


def _save_members():
    with open(MEMBERS_FILE, "w") as f:
        json.dump({str(k): {str(uid): name for uid, name in v.items()}
                    for k, v in group_members.items()}, f)

bot = telebot.TeleBot(BOT_TOKEN)

# Persistent store: chat_id -> {user_id: display_name}
group_members: dict[int, dict[int, str]] = _load_members()

def _mention(name: str, telegram_id: str | None = None, chat_id: int | None = None) -> str:
    """Build a proper Telegram mention using tg://user?id= link.
    Falls back to bold name if no ID available."""
    tid = telegram_id
    if not tid and chat_id:
        for uid, uname in group_members.get(chat_id, {}).items():
            if uname == name:
                tid = str(uid)
                break
    if tid and tid != "0":
        return f'<a href="tg://user?id={tid}">{name}</a>'
    # No ID — treat as a username handle
    return f"@{name}"


# ---------------------------------------------------------------------------
# Flask API + static file serving
# ---------------------------------------------------------------------------
DIST_DIR = os.path.join(os.path.dirname(__file__), "splitwize-spark", "dist")
app = Flask(__name__, static_folder=DIST_DIR, static_url_path="")
CORS(app)


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_spa(path):
    """Serve the React SPA — any non-API route returns index.html."""
    if path and os.path.exists(os.path.join(DIST_DIR, path)):
        return app.send_static_file(path)
    return app.send_static_file("index.html")


@app.route("/api/sessions", methods=["POST"])
def api_create_session():
    data = request.json
    if not data:
        return jsonify({"error": "Missing JSON body"}), 400
    session = db.create_session(
        event_name=data["event_name"],
        bill_amount=data["bill_amount"],
        payee=data["payee"],
        even_split=data.get("even_split", True),
        participants=data["participants"],
        chat_id=data.get("chat_id"),
        payee_phone=data.get("payee_phone"),
        payee_amount=data.get("payee_amount"),
    )

    # Announce in group chat if chat_id provided
    chat_id = data.get("chat_id")
    print(f"[SESSION] Created {session['id']}, chat_id={chat_id}, participants={[p['name'] for p in data['participants']]}")
    if chat_id:
        try:
            cid = int(chat_id)
            session_url = f"{WEBAPP_URL}?session={session['id']}"
            print(f"[BOT] Sending group message to chat {cid}")

            # Build breakdown lines — include payee's share
            payee_amount = data.get("payee_amount", "0.00")
            payee_mention = _mention(data["payee"], chat_id=cid)
            breakdown_lines = [f"  • {payee_mention} — <b>${payee_amount}</b> (payee)"]
            breakdown_lines += [
                f"  • {_mention(p['name'], p.get('telegram_id'), cid)} — <b>${p['amount']}</b>"
                for p in data["participants"]
            ]
            breakdown = "\n".join(breakdown_lines)
            total = data["bill_amount"]

            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("💰 View Split", url=session_url))
            bot.send_message(
                cid,
                f"📢 <b>Bill Split Created!</b>\n\n"
                f"Event: <b>{data['event_name']}</b>\n"
                f"Total: <b>${total}</b>\n"
                f"Organized by: {payee_mention}\n\n"
                f"💸 <b>Who owes what:</b>\n{breakdown}",
                parse_mode="HTML",
                reply_markup=kb,
            )
            print(f"[BOT] Group message sent OK")

            # Send whisper-style QR buttons per participant
            for p in data["participants"]:
                try:
                    tid = p.get("telegram_id")
                    if not tid:
                        for uid, name in group_members.get(cid, {}).items():
                            if name == p["name"]:
                                tid = str(uid)
                                break
                    callback_id = f"qr:{session['id']}:{p['name']}:{tid or '0'}"
                    whisper_kb = types.InlineKeyboardMarkup()
                    whisper_kb.add(types.InlineKeyboardButton(
                        "🔒 Show my QR code",
                        callback_data=callback_id,
                    ))
                    p_mention = _mention(p["name"], tid, cid)
                    bot.send_message(
                        cid,
                        f"💸 {p_mention} owes <b>${p['amount']}</b>\n"
                        f"🔒 Only {p_mention} can reveal their PayNow QR code.",
                        parse_mode="HTML",
                        reply_markup=whisper_kb,
                    )
                    print(f"[BOT] Whisper QR button sent for {p['name']}")
                except Exception as e:
                    print(f"[BOT ERROR] Whisper for {p['name']} failed: {e}")
        except Exception as e:
            print(f"[BOT ERROR] Failed to send group/DM messages: {e}")

    return jsonify(session), 201


@app.route("/api/sessions/<session_id>", methods=["GET"])
def api_get_session(session_id):
    session = db.get_session(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    return jsonify(session)


@app.route("/api/sessions/<session_id>/participants/<name>/status", methods=["PATCH"])
def api_update_status(session_id, name):
    data = request.json
    if not data or "status" not in data:
        return jsonify({"error": "Missing status"}), 400
    ok = db.update_participant_status(session_id, name, data["status"])
    if not ok:
        return jsonify({"error": "Participant not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/sessions/<session_id>/participants/<name>/screenshot", methods=["POST"])
def api_upload_screenshot(session_id, name):
    if "screenshot" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    f = request.files["screenshot"]
    ext = os.path.splitext(f.filename)[1] if f.filename else ".png"
    filename = f"{session_id}_{name}{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)
    f.save(filepath)
    db.save_screenshot_path(session_id, name, filepath)
    # Auto-mark as paid on screenshot upload
    db.update_participant_status(session_id, name, "paid")
    return jsonify({"ok": True, "path": filepath})


@app.route("/api/sessions/<session_id>/remind", methods=["POST"])
def api_remind(session_id):
    session = db.get_session(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    chat_id = session.get("chat_id")
    unpaid = [p for p in session["participants"] if p["status"] != "paid"]
    if not unpaid:
        return jsonify({"ok": True, "reminded": []})
    if chat_id:
        try:
            cid = int(chat_id)
            names = "\n".join(
                f"  • {_mention(p['name'], p.get('telegram_id'), cid)} — <b>${p['amount']}</b>"
                for p in unpaid
            )
            payee_mention = _mention(session["payee"], chat_id=cid)
            session_url = f"{WEBAPP_URL}?session={session_id}"
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("💰 View Split", url=session_url))
            bot.send_message(
                cid,
                f"🔔 <b>Payment Reminder</b>\n\n"
                f"For <b>{session['event_name']}</b> organized by {payee_mention}:\n\n"
                f"{names}\n\n"
                f"Please pay soon! 🙏",
                parse_mode="HTML",
                reply_markup=kb,
            )
            print(f"[BOT] Reminder sent to group {cid} for {len(unpaid)} unpaid")
        except Exception as e:
            print(f"[BOT ERROR] Reminder failed: {e}")
    return jsonify({"ok": True, "reminded": [p["name"] for p in unpaid]})


@app.route("/api/sessions/<session_id>/participants/<name>/self-confirm", methods=["POST"])
def api_self_confirm(session_id, name):
    """Participant confirms their own payment. Only allowed if they've read the whisper."""
    session = db.get_session(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    participant = next((p for p in session["participants"] if p["name"] == name), None)
    if not participant:
        return jsonify({"error": "Participant not found"}), 404
    if not participant.get("whisper_read"):
        return jsonify({"error": "You must view your QR code first"}), 403
    db.update_participant_status(session_id, name, "self-confirmed")
    # Notify group chat
    chat_id = session.get("chat_id")
    if chat_id:
        try:
            cid = int(chat_id)
            p_mention = _mention(name, participant.get("telegram_id"), cid)
            bot.send_message(
                cid,
                f"💸 {p_mention} says they've paid <b>${participant['amount']}</b>.\n"
                f"Awaiting confirmation from {_mention(session['payee'], chat_id=cid)}.",
                parse_mode="HTML",
            )
        except Exception as e:
            print(f"[BOT ERROR] Self-confirm notify failed: {e}")
    return jsonify({"ok": True, "status": "self-confirmed"})


@app.route("/api/sessions/<session_id>/qr/<name>", methods=["GET"])
def api_qr(session_id, name):
    session = db.get_session(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    participant = next((p for p in session["participants"] if p["name"] == name), None)
    if not participant:
        return jsonify({"error": "Participant not found"}), 404

    # Generate PayNow QR code with phone number if available
    payee_phone = session.get("payee_phone", "")
    if payee_phone:
        qr_data = generate_paynow_qr_data(
            phone=payee_phone,
            amount=participant["amount"],
            payee_name=session["payee"],
        )
    else:
        qr_data = (
            f"PayNow to @{session['payee']}\n"
            f"Amount: ${participant['amount']}\n"
            f"Event: {session['event_name']}"
        )
    img = qrcode.make(qr_data)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    from datetime import datetime
    date_str = datetime.utcnow().strftime("%Y%m%d")
    event_clean = session["event_name"].replace(" ", "_")[:20]
    filename = f"PayNow_QR_{name}_{event_clean}_{date_str}.png"
    response = send_file(buf, mimetype="image/png", download_name=filename)
    response.headers["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


@app.route("/api/sessions/<session_id>/pay/<name>", methods=["GET"])
def api_pay_page(session_id, name):
    """Render a payment page with QR code, payee details, and download button."""
    session = db.get_session(session_id)
    if not session:
        return "Session not found", 404
    participant = next((p for p in session["participants"] if p["name"] == name), None)
    if not participant:
        return "Participant not found", 404

    payee_phone = session.get("payee_phone", "")
    phone_display = f"+65 {payee_phone[:4]} {payee_phone[4:]}" if payee_phone else "N/A"
    qr_img_url = f"/api/sessions/{session_id}/qr/{quote(name)}"
    from datetime import datetime
    date_str = datetime.utcnow().strftime("%Y%m%d")
    event_clean = session["event_name"].replace(" ", "_")[:20]
    filename = f"PayNow_QR_{name}_{event_clean}_{date_str}.png"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PayNow — {name}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: linear-gradient(135deg, #0f172a, #1e3a5f, #0f172a); min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; color: white; }}
  .card {{ background: rgba(255,255,255,0.05); backdrop-filter: blur(10px); border: 1px solid rgba(255,255,255,0.1); border-radius: 24px; padding: 32px; max-width: 380px; width: 100%; text-align: center; }}
  .title {{ font-size: 14px; color: rgba(255,255,255,0.5); text-transform: uppercase; letter-spacing: 2px; margin-bottom: 8px; }}
  .amount {{ font-size: 48px; font-weight: 700; background: linear-gradient(135deg, #4ade80, #22d3ee); -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 24px; }}
  .qr-wrap {{ background: white; border-radius: 16px; padding: 20px; margin-bottom: 24px; }}
  .qr-wrap img {{ width: 100%; max-width: 280px; height: auto; }}
  .details {{ background: rgba(255,255,255,0.05); border-radius: 12px; padding: 16px; margin-bottom: 24px; text-align: left; }}
  .row {{ display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid rgba(255,255,255,0.1); }}
  .row:last-child {{ border: none; }}
  .label {{ color: rgba(255,255,255,0.5); font-size: 14px; }}
  .value {{ color: white; font-weight: 600; font-size: 14px; font-family: 'SF Mono', monospace; }}
  .btn {{ display: block; width: 100%; padding: 16px; border-radius: 12px; font-size: 16px; font-weight: 600; text-decoration: none; text-align: center; transition: all 0.2s; cursor: pointer; border: none; }}
  .btn-primary {{ background: linear-gradient(135deg, #3b82f6, #1d4ed8); color: white; margin-bottom: 12px; }}
  .btn-primary:hover {{ transform: translateY(-2px); box-shadow: 0 8px 20px rgba(59,130,246,0.4); }}
  .btn-secondary {{ background: rgba(255,255,255,0.1); color: white; border: 1px solid rgba(255,255,255,0.2); }}
  .btn-secondary:hover {{ background: rgba(255,255,255,0.15); }}
  .btn-confirm {{ background: linear-gradient(135deg, #22c55e, #16a34a); color: white; margin-top: 12px; }}
  .btn-confirm:hover {{ transform: translateY(-2px); box-shadow: 0 8px 20px rgba(34,197,94,0.4); }}
  .btn-confirm:disabled {{ transform: none; box-shadow: none; }}
  .btn-pending {{ background: linear-gradient(135deg, #f59e0b, #d97706); }}
  .event {{ color: rgba(255,255,255,0.4); font-size: 12px; margin-top: 16px; }}
</style>
</head>
<body>
<div class="card">
  <div class="title">PayNow Payment</div>
  <div class="amount">${participant['amount']}</div>
  <div class="qr-wrap">
    <img src="{qr_img_url}" alt="PayNow QR Code">
  </div>
  <div class="details">
    <div class="row"><span class="label">Pay to</span><span class="value">{session['payee']}</span></div>
    <div class="row"><span class="label">PayNow</span><span class="value">{phone_display}</span></div>
    <div class="row"><span class="label">Amount</span><span class="value">${participant['amount']}</span></div>
    <div class="row"><span class="label">Event</span><span class="value">{session['event_name']}</span></div>
  </div>
  <a href="{qr_img_url}" download="{filename}" class="btn btn-primary">📥 Download QR Image</a>
  <button id="confirm-btn" class="btn btn-confirm" onclick="confirmPayment()" {'' if participant.get('whisper_read') else 'disabled style="opacity:0.4;cursor:not-allowed"'}>
    {('✅ Payment Confirmed' if participant['status'] == 'paid' else '⏳ Awaiting Payee Confirmation' if participant['status'] == 'self-confirmed' else "✅ I've Paid")}
  </button>
  <div class="event">GroupPay — {session['event_name']}</div>
</div>
<script>
async function confirmPayment() {{
  const btn = document.getElementById('confirm-btn');
  if (btn.disabled) return;
  btn.disabled = true;
  btn.textContent = 'Confirming...';
  try {{
    const res = await fetch('/api/sessions/{session_id}/participants/{quote(name)}/self-confirm', {{method: 'POST'}});
    const data = await res.json();
    if (res.ok) {{
      btn.textContent = '⏳ Awaiting Payee Confirmation';
      btn.classList.add('btn-pending');
    }} else {{
      btn.textContent = data.error || 'Error';
      btn.disabled = false;
    }}
  }} catch {{
    btn.textContent = 'Error — try again';
    btn.disabled = false;
  }}
}}
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Telegram Bot Handlers
# ---------------------------------------------------------------------------

def _track_member(msg):
    """Track the sender of any message and persist to disk."""
    if msg.chat.type in ("group", "supergroup") and msg.from_user:
        chat_id = msg.chat.id
        if chat_id not in group_members:
            group_members[chat_id] = {}
        name = msg.from_user.first_name or msg.from_user.username or str(msg.from_user.id)
        group_members[chat_id][msg.from_user.id] = name
        _save_members()


def _build_webapp_url(chat_id: int) -> str:
    """Build webapp URL with known members and chat_id as query params."""
    members = group_members.get(chat_id, {})
    parts_list = [f"chat_id={chat_id}"]
    if members:
        member_str = ",".join(f"{quote(name)}:{uid}" for uid, name in members.items())
        parts_list.append(f"members={member_str}")
    sep = "&" if "?" in WEBAPP_URL else "?"
    return f"{WEBAPP_URL}{sep}{'&'.join(parts_list)}"


def _make_keyboard(msg) -> types.InlineKeyboardMarkup:
    """Create the Open GroupPay button — web_app in private, url in groups."""
    url = _build_webapp_url(msg.chat.id)
    kb = types.InlineKeyboardMarkup()
    if msg.chat.type == "private":
        kb.add(types.InlineKeyboardButton(
            "💰 Open GroupPay",
            web_app=types.WebAppInfo(url=url),
        ))
    else:
        kb.add(types.InlineKeyboardButton(
            "💰 Open GroupPay",
            url=url,
        ))
    return kb


@bot.callback_query_handler(func=lambda call: call.data.startswith("qr:"))
def handle_qr_whisper(call):
    """Handle whisper QR button clicks — only show QR to the intended recipient."""
    parts = call.data.split(":", 3)  # qr:session_id:name:telegram_id
    if len(parts) < 4:
        bot.answer_callback_query(call.id, "Invalid QR data.", show_alert=True)
        return

    _, session_id, target_name, target_tid = parts
    clicker = call.from_user

    print(f"[WHISPER] Click by {clicker.first_name} (id={clicker.id}, username={clicker.username}) — target: {target_name} (tid={target_tid})")

    # Check if clicker is the intended recipient
    is_target = (
        str(clicker.id) == target_tid
        or (clicker.first_name or "").lower() == target_name.lower()
        or (clicker.username or "").lower() == target_name.lower()
    )

    if not is_target:
        bot.answer_callback_query(
            call.id,
            f"🚫 This QR code is for {target_name} only.",
            show_alert=True,
        )
        return

    session = db.get_session(session_id)
    if not session:
        bot.answer_callback_query(call.id, "Session expired.", show_alert=True)
        return

    participant = next((p for p in session["participants"] if p["name"] == target_name), None)
    if not participant:
        bot.answer_callback_query(call.id, "Participant not found.", show_alert=True)
        return

    payee_phone = session.get("payee_phone", "")
    phone_display = f"+65 {payee_phone[:4]} {payee_phone[4:]}" if payee_phone else "N/A"

    bot.answer_callback_query(call.id, "🔓 QR code unlocked!")

    # Mark whisper as read
    db.mark_whisper_read(session_id, target_name)

    # Replace button with payment details + QR page link
    qr_page_url = f"{WEBAPP_URL}/api/sessions/{session_id}/pay/{quote(target_name)}"
    p_mention = _mention(target_name, target_tid)
    try:
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("📲 Open PayNow QR", url=qr_page_url))
        bot.edit_message_text(
            f"💸 {p_mention} owes <b>${participant['amount']}</b>\n"
            f"💰 Pay to: <b>{session['payee']}</b>\n"
            f"📲 PayNow: <b>{phone_display}</b>\n\n"
            f"👇 Tap below to view your QR code",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML",
            reply_markup=kb,
        )
    except Exception:
        pass


@bot.message_handler(commands=["start"])
def cmd_start(msg):
    _track_member(msg)
    bot.send_message(
        msg.chat.id,
        "👋 *Welcome to GroupPay!*\n\n"
        "Split bills quickly and fairly with your group.\n\n"
        "Tap the button below or use /split to open the app.",
        parse_mode="Markdown",
        reply_markup=_make_keyboard(msg),
    )


@bot.message_handler(commands=["split"])
def cmd_split(msg):
    _track_member(msg)
    bot.send_message(
        msg.chat.id,
        "📢 *Let's split a bill!*\n\nTap below to open GroupPay:",
        parse_mode="Markdown",
        reply_markup=_make_keyboard(msg),
    )


@bot.message_handler(commands=["help"])
def cmd_help(msg):
    _track_member(msg)
    bot.send_message(
        msg.chat.id,
        "*GroupPay Commands*\n\n"
        "/start — Welcome & open the app\n"
        "/split — Start a new bill split\n"
        "/help — Show this help message",
        parse_mode="Markdown",
    )


@bot.message_handler(content_types=["new_chat_members"])
def handle_new_members(msg):
    if msg.chat.type in ("group", "supergroup"):
        chat_id = msg.chat.id
        if chat_id not in group_members:
            group_members[chat_id] = {}
        for user in msg.new_chat_members:
            name = user.first_name or user.username or str(user.id)
            group_members[chat_id][user.id] = name
        _save_members()


@bot.message_handler(content_types=["left_chat_member"])
def handle_left_member(msg):
    if msg.chat.type in ("group", "supergroup") and msg.left_chat_member:
        chat_id = msg.chat.id
        group_members.get(chat_id, {}).pop(msg.left_chat_member.id, None)
        _save_members()


@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(msg):
    _track_member(msg)
    if msg.chat.type in ("group", "supergroup"):
        return
    bot.send_message(
        msg.chat.id,
        "Use /split to open GroupPay, or tap the button below:",
        reply_markup=_make_keyboard(msg),
    )


@bot.message_handler(content_types=["web_app_data"])
def handle_webapp_data(msg):
    _track_member(msg)
    bot.send_message(
        msg.chat.id,
        f"✅ Received data from GroupPay:\n`{msg.web_app_data.data}`",
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    db.init_db()
    print(f"🤖 GroupPay Bot running — Mini App URL: {WEBAPP_URL}")
    print("🌐 Flask API running on http://0.0.0.0:5000")

    # Run Flask in a background thread
    flask_thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=5000, debug=False),
        daemon=True,
    )
    flask_thread.start()

    bot.infinity_polling(skip_pending=True)
