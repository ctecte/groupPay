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

BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBAPP_URL = os.environ["WEBAPP_URL"]
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

MEMBERS_FILE = os.path.join(DATA_DIR, "group_members.json")


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


def _log_message(label: str, msg):
    user = getattr(msg, "from_user", None)
    user_id = getattr(user, "id", None)
    username = getattr(user, "username", None)
    thread_id = getattr(msg, "message_thread_id", None)
    print(
        f"[{label}] chat_id={msg.chat.id} chat_type={msg.chat.type} "
        f"thread_id={thread_id} user_id={user_id} username={username} "
        f"text={getattr(msg, 'text', None)!r}",
        flush=True,
    )

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


@app.route("/api/members/<chat_id>")
def api_get_members(chat_id):
    members = group_members.get(int(chat_id), {})
    return jsonify([{"name": name, "id": str(uid)} for uid, name in members.items()])


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

    # Diagnostic logging for duplicate-session investigation.
    # Captures who/what/where so two POSTs can be compared: same client+payload
    # within seconds (retry/replay) vs. genuinely separate opens minutes apart.
    import hashlib
    _fp_src = json.dumps({
        "chat_id": data.get("chat_id"),
        "thread_id": data.get("thread_id"),
        "payee": data.get("payee"),
        "bill_amount": data.get("bill_amount"),
        "participants": sorted(
            (p.get("name"), p.get("amount")) for p in data.get("participants", [])
        ),
    }, sort_keys=True, default=str)
    _fp = hashlib.sha1(_fp_src.encode()).hexdigest()[:10]
    _ip = (request.headers.get("CF-Connecting-IP")
           or request.headers.get("X-Forwarded-For")
           or request.remote_addr)
    _ua = request.headers.get("User-Agent", "")
    print(
        f"[SESSION REQ] fp={_fp} ip={_ip} ua={_ua!r} "
        f"chat_id={data.get('chat_id')} thread_id={data.get('thread_id')} "
        f"event={data.get('event_name')!r} bill={data.get('bill_amount')}",
        flush=True,
    )

    # Persist itemized receipt + assignments (only present for OCR-scanned bills)
    # as a JSON blob for display-only cross-checking on the View Split page.
    items = data.get("items")
    items_json = json.dumps(items) if items else None

    session = db.create_session(
        event_name=data["event_name"],
        bill_amount=data["bill_amount"],
        payee=data["payee"],
        even_split=data.get("even_split", True),
        participants=data["participants"],
        chat_id=data.get("chat_id"),
        thread_id=data.get("thread_id"),
        payee_phone=data.get("payee_phone"),
        payee_amount=data.get("payee_amount"),
        payee_telegram_id=data.get("payee_telegram_id"),
        items_json=items_json,
    )

    # Announce in group chat if chat_id provided
    chat_id = data.get("chat_id")
    tid_kwargs = {}
    thread_id = data.get("thread_id")
    if thread_id:
        tid_kwargs["message_thread_id"] = int(thread_id)
    print(f"[SESSION] Created {session['id']} fp={_fp}, chat_id={chat_id}, thread_id={thread_id}, participants={[p['name'] for p in data['participants']]}", flush=True)
    if chat_id:
        try:
            cid = int(chat_id)
            # Build session URL with members so the frontend can identify the viewer
            base_url = _build_webapp_url(cid, int(thread_id) if thread_id else None)
            sep = "&" if "?" in base_url else "?"
            session_url = f"{base_url}{sep}session={session['id']}"
            print(f"[BOT] Sending group message to chat {cid} thread {thread_id}")

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
                **tid_kwargs,
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
                    callback_id = f"qr|{session['id']}|{p['name']}|{tid or '0'}"
                    whisper_kb = types.InlineKeyboardMarkup()
                    whisper_kb.add(types.InlineKeyboardButton(
                        "🔒 Show my QR code",
                        callback_data=callback_id,
                    ))
                    p_mention = _mention(p["name"], tid, cid)
                    sent_msg = bot.send_message(
                        cid,
                        f"💸 {p_mention} owes <b>${p['amount']}</b>\n"
                        f"🔒 Only {p_mention} can reveal their PayNow QR code.",
                        parse_mode="HTML",
                        reply_markup=whisper_kb,
                        **tid_kwargs,
                    )
                    db.save_whisper_msg_id(session["id"], p["name"], str(sent_msg.message_id))
                    print(f"[BOT] Whisper QR button sent for {p['name']} (msg_id={sent_msg.message_id})")
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
    # Add screenshot URLs for participants that have uploaded
    for p in session["participants"]:
        if p.get("screenshot_path"):
            p["screenshot_url"] = f"/api/sessions/{session_id}/participants/{quote(p['name'])}/screenshot"
        else:
            p["screenshot_url"] = None
    # Parse the itemized breakdown JSON blob back into an object (None if not OCR)
    items_raw = session.pop("items_json", None)
    session["items"] = json.loads(items_raw) if items_raw else None
    return jsonify(session)


@app.route("/api/sessions/<session_id>/participants/<name>/status", methods=["PATCH"])
def api_update_status(session_id, name):
    data = request.json
    if not data or "status" not in data:
        return jsonify({"error": "Missing status"}), 400
    # Only the payee can mark payments — verify by Telegram ID
    caller_tid = data.get("telegram_id")
    if caller_tid:
        session = db.get_session(session_id)
        if session and session.get("payee_telegram_id") and str(caller_tid) != str(session["payee_telegram_id"]):
            return jsonify({"error": "Only the payee can mark payments"}), 403
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
    db.update_participant_status(session_id, name, "paid")

    # Edit the original whisper message to show paid status
    session = db.get_session(session_id)
    if session:
        participant = next((p for p in session["participants"] if p["name"] == name), None)
        if participant and participant.get("whisper_msg_id"):
            chat_id = session.get("chat_id")
            if chat_id:
                tid = participant.get("telegram_id")
                p_mention = _mention(name, tid, chat_id)
                try:
                    bot.edit_message_text(
                        f"✅ {p_mention} paid <b>${participant['amount']}</b> — screenshot submitted",
                        chat_id=chat_id,
                        message_id=int(participant["whisper_msg_id"]),
                        parse_mode="HTML",
                    )
                except Exception as e:
                    print(f"[BOT] Failed to edit whisper message: {e}")

    return jsonify({"ok": True, "path": filepath})


@app.route("/api/sessions/<session_id>/participants/<name>/screenshot", methods=["GET"])
def api_get_screenshot(session_id, name):
    """Serve a participant's payment screenshot."""
    session = db.get_session(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    participant = next((p for p in session["participants"] if p["name"] == name), None)
    if not participant or not participant.get("screenshot_path"):
        return jsonify({"error": "No screenshot"}), 404
    path = participant["screenshot_path"]
    if not os.path.exists(path):
        return jsonify({"error": "File not found"}), 404
    return send_file(path, mimetype="image/jpeg")


@app.route("/api/sessions/<session_id>/remind", methods=["POST"])
def api_remind(session_id):
    session = db.get_session(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    chat_id = session.get("chat_id")
    tid_kwargs = {}
    if session.get("thread_id"):
        tid_kwargs["message_thread_id"] = int(session["thread_id"])
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
            thread_id = session.get("thread_id")
            base_url = _build_webapp_url(cid, int(thread_id) if thread_id else None)
            sep = "&" if "?" in base_url else "?"
            session_url = f"{base_url}{sep}session={session_id}"
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
                **tid_kwargs,
            )
            print(f"[BOT] Reminder sent to group {cid} for {len(unpaid)} unpaid")
        except Exception as e:
            print(f"[BOT ERROR] Reminder failed: {e}")
    return jsonify({"ok": True, "reminded": [p["name"] for p in unpaid]})


@app.route("/api/sessions/<session_id>/auto-remind", methods=["POST"])
def api_auto_remind(session_id):
    """Set or cancel auto-remind for a session."""
    data = request.json
    if not data:
        return jsonify({"error": "Missing JSON body"}), 400
    hours = data.get("hours")
    if hours is None:
        # Cancel
        db.cancel_auto_remind(session_id)
        return jsonify({"ok": True, "auto_remind": None})
    hours = float(hours)
    if hours <= 0:
        return jsonify({"error": "Invalid reminder interval"}), 400
    db.set_auto_remind(session_id, hours)
    return jsonify({"ok": True, "auto_remind_hours": hours})


@app.route("/api/ocr", methods=["POST"])
def api_ocr():
    """Process a receipt image with Gemini flash, with PaddleOCR as backup, and return extracted items."""
    if "receipt" not in request.files:
        return jsonify({"error": "No image uploaded"}), 400

    f = request.files["receipt"]
    image_bytes = f.read()
    print(f"[OCR] Received image: {len(image_bytes)} bytes")
    # Debug: save uploaded image for inspection
    debug_path = os.path.join(UPLOAD_DIR, "ocr_latest.jpg")
    with open(debug_path, "wb") as df:
        df.write(image_bytes)

    if len(image_bytes) > 10 * 1024 * 1024:
        return jsonify({"error": "Image too large (max 10MB)"}), 400

    try:
        from ocr import run_ocr
        result = run_ocr(image_bytes)
    except Exception as e:
        import traceback
        print(f"[OCR ERROR] {e}")
        traceback.print_exc()
        return jsonify({"error": "OCR processing failed. Please try again."}), 500

    items = result.get("items", [])
    add_on_charges = result.get("add_on_charges", [])
    informational_charges = result.get("informational_charges", [])
    discounts = result.get("discounts", [])
    service_charge_rate = result.get("service_charge_rate")
    gst_rate = result.get("gst_rate")
    receipt_subtotal = result.get("receipt_subtotal")
    receipt_grand_total = result.get("receipt_grand_total")
    charges = result.get("charges", add_on_charges + informational_charges)
    charges_included = result.get(
        "charges_included",
        bool(informational_charges) and not bool(add_on_charges),
    )

    if not items:
        return jsonify({"error": "No items found on receipt. Try a clearer photo."})

    computed_subtotal = round(sum(item["price"] * item["qty"] for item in items), 2)
    computed_add_on_total = round(sum(c["price"] for c in add_on_charges), 2)
    computed_discount_total = round(sum(d["price"] for d in discounts), 2)
    subtotal = round(receipt_subtotal, 2) if isinstance(receipt_subtotal, (int, float)) else computed_subtotal
    computed_total = round(computed_subtotal - computed_discount_total + computed_add_on_total, 2)
    total = round(receipt_grand_total, 2) if isinstance(receipt_grand_total, (int, float)) else computed_total
    return jsonify({
        "items": items,
        "add_on_charges": add_on_charges,
        "informational_charges": informational_charges,
        "discounts": discounts,
        "service_charge_rate": service_charge_rate,
        "gst_rate": gst_rate,
        "charges": charges,
        "charges_included": charges_included,
        "subtotal": subtotal,
        "total": total,
        "computed_total": computed_total,
        "receipt_grand_total": round(receipt_grand_total, 2) if isinstance(receipt_grand_total, (int, float)) else None,
    })


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
    tid_kwargs = {}
    if session.get("thread_id"):
        tid_kwargs["message_thread_id"] = int(session["thread_id"])
    if chat_id:
        try:
            cid = int(chat_id)
            p_mention = _mention(name, participant.get("telegram_id"), cid)
            bot.send_message(
                cid,
                f"💸 {p_mention} says they've paid <b>${participant['amount']}</b>.\n"
                f"Awaiting confirmation from {_mention(session['payee'], chat_id=cid)}.",
                parse_mode="HTML",
                **tid_kwargs,
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
            reference=participant.get("payment_ref", ""),
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
    # Sanitize filename to ASCII only (emoji/unicode chars break HTTP headers)
    name_clean = name.encode("ascii", "ignore").decode("ascii").strip() or "user"
    event_clean = event_clean.encode("ascii", "ignore").decode("ascii").strip() or "event"
    filename = f"PayNow_QR_{name_clean}_{event_clean}_{date_str}.png"
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
    phone_display = f"+65 XXXX {payee_phone[-4:]}" if payee_phone else "N/A"
    qr_img_url = f"/api/sessions/{session_id}/qr/{quote(name)}"
    from datetime import datetime
    date_str = datetime.utcnow().strftime("%Y%m%d")
    event_clean = session["event_name"].replace(" ", "_")[:20]
    name_clean = name.encode("ascii", "ignore").decode("ascii").strip() or "user"
    event_clean = event_clean.encode("ascii", "ignore").decode("ascii").strip() or "event"
    filename = f"PayNow_QR_{name_clean}_{event_clean}_{date_str}.png"

    payment_ref = participant.get("payment_ref", "")
    is_paid = participant["status"] == "paid"
    # Human-readable generation date from the session's created_at (fallback to today)
    try:
        gen_date = datetime.fromisoformat(session["created_at"]).strftime("%d %b %Y")
    except Exception:
        gen_date = datetime.utcnow().strftime("%d %b %Y")

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
  .btn-upload {{ background: linear-gradient(135deg, #22c55e, #16a34a); color: white; margin-top: 12px; position: relative; overflow: hidden; }}
  .btn-upload:hover {{ transform: translateY(-2px); box-shadow: 0 8px 20px rgba(34,197,94,0.4); }}
  .btn-upload input {{ position: absolute; inset: 0; opacity: 0; cursor: pointer; }}
  .btn-paid {{ background: linear-gradient(135deg, #22c55e, #16a34a); color: white; margin-top: 12px; opacity: 0.9; }}
  .ref {{ background: rgba(255,255,255,0.08); border-radius: 8px; padding: 8px 12px; margin-bottom: 16px; font-size: 12px; color: rgba(255,255,255,0.5); }}
  .ref code {{ color: #4ade80; font-weight: 600; font-family: 'SF Mono', monospace; }}
  .event {{ color: rgba(255,255,255,0.4); font-size: 12px; margin-top: 16px; }}
  .status-msg {{ padding: 12px; border-radius: 10px; margin-top: 12px; font-size: 14px; }}
  .status-ok {{ background: rgba(34,197,94,0.15); border: 1px solid rgba(34,197,94,0.3); color: #4ade80; }}
  .status-err {{ background: rgba(239,68,68,0.15); border: 1px solid rgba(239,68,68,0.3); color: #f87171; }}
  .spinner {{ display: inline-block; width: 18px; height: 18px; border: 2px solid white; border-top-color: transparent; border-radius: 50%; animation: spin 0.6s linear infinite; vertical-align: middle; margin-right: 8px; }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
</style>
</head>
<body>
<div class="card">
  <div class="title">{session['event_name']}</div>
  <div class="amount">${participant['amount']}</div>
  {'<div class="status-msg status-ok">✅ Payment screenshot submitted</div>' if is_paid else f"""
  <div class="qr-wrap">
    <img src="{qr_img_url}" alt="PayNow QR Code">
  </div>
  <div class="details">
    <div class="row"><span class="label">Pay to</span><span class="value">{session['payee']}</span></div>
    <div class="row"><span class="label">PayNow</span><span class="value">{phone_display}</span></div>
    <div class="row"><span class="label">Generated</span><span class="value">{gen_date}</span></div>
  </div>
  <a href="{qr_img_url}" download="{filename}" class="btn btn-primary">📥 Download QR Image</a>
  <div id="upload-area">
    <label class="btn btn-upload" id="upload-btn">
      📸 Upload Payment Screenshot
      <input type="file" accept="image/*" onchange="uploadScreenshot(this)">
    </label>
  </div>
  <div id="status"></div>
  """}
</div>
<script>
async function uploadScreenshot(input) {{
  const file = input.files[0];
  if (!file) return;
  const btn = document.getElementById('upload-btn');
  const status = document.getElementById('status');
  btn.innerHTML = '<span class="spinner"></span>Uploading...';
  const formData = new FormData();
  formData.append('screenshot', file);
  try {{
    const res = await fetch('/api/sessions/{session_id}/participants/{quote(name)}/screenshot', {{
      method: 'POST',
      body: formData,
    }});
    const data = await res.json();
    if (res.ok) {{
      btn.outerHTML = '<div class="btn btn-paid">✅ Screenshot Submitted</div>';
      status.innerHTML = '<div class="status-msg status-ok">Payment recorded — you\\'re all set!</div>';
    }} else {{
      status.innerHTML = '<div class="status-msg status-err">' + (data.error || 'Upload failed') + '</div>';
      btn.innerHTML = '📸 Upload Payment Screenshot<input type="file" accept="image/*" onchange="uploadScreenshot(this)">';
    }}
  }} catch {{
    status.innerHTML = '<div class="status-msg status-err">Upload failed — try again</div>';
    btn.innerHTML = '📸 Upload Payment Screenshot<input type="file" accept="image/*" onchange="uploadScreenshot(this)">';
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


def _build_webapp_url(chat_id: int, thread_id: int | None = None) -> str:
    """Build webapp URL with chat_id and thread_id as query params."""
    parts_list = [f"chat_id={chat_id}"]
    if thread_id:
        parts_list.append(f"thread_id={thread_id}")
    sep = "&" if "?" in WEBAPP_URL else "?"
    return f"{WEBAPP_URL}{sep}{'&'.join(parts_list)}"


def _make_keyboard(msg) -> types.InlineKeyboardMarkup:
    """Create the Open GroupPay button — web_app in private, url in groups."""
    thread_id = getattr(msg, 'message_thread_id', None)
    url = _build_webapp_url(msg.chat.id, thread_id)
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


@bot.callback_query_handler(func=lambda call: call.data.startswith("qr|") or call.data.startswith("qr:"))
def handle_qr_whisper(call):
    """Handle whisper QR button clicks — only show QR to the intended recipient."""
    delim = "|" if "|" in call.data else ":"
    parts = call.data.split(delim, 3)  # qr|session_id|name|telegram_id
    if len(parts) < 4:
        bot.answer_callback_query(call.id, "Invalid QR data.", show_alert=True)
        return

    _, session_id, target_name, target_tid = parts
    clicker = call.from_user

    print(f"[WHISPER] Click by {clicker.first_name} (id={clicker.id}, username={clicker.username}) — target: {target_name} (tid={target_tid})")

    # Check if clicker is the intended recipient
    # Telegram ID is the strongest check; username (@tag) is unique and stable;
    # first_name is NOT checked when ID is known (easily duplicated)
    if target_tid and target_tid != "0":
        is_target = (
            str(clicker.id) == target_tid
            or (clicker.username or "").lower() == target_name.lower()
        )
    else:
        is_target = (
            (clicker.first_name or "").lower() == target_name.lower()
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

    bot.answer_callback_query(call.id, "🔓 QR code unlocked!")

    # Mark whisper as read
    db.mark_whisper_read(session_id, target_name)

    # Replace button with payment details + QR page link
    qr_page_url = f"{WEBAPP_URL}/api/sessions/{session_id}/pay/{quote(target_name)}"
    p_mention = _mention(target_name, target_tid)
    try:
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("📲 Open PayNow QR", url=qr_page_url))
        payee_mention = _mention(session["payee"], chat_id=call.message.chat.id)
        bot.edit_message_text(
            f"💸 {p_mention} owes <b>${participant['amount']}</b>\n"
            f"💰 Pay to: {payee_mention}\n\n"
            f"👇 Tap below to view your QR code",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML",
            reply_markup=kb,
        )
    except Exception:
        pass


def _thread_kwargs(msg) -> dict:
    """Return message_thread_id kwarg if the message is in a forum topic."""
    tid = getattr(msg, 'message_thread_id', None)
    return {"message_thread_id": tid} if tid else {}


@bot.message_handler(commands=["start"])
def cmd_start(msg):
    _log_message("CMD_START", msg)
    _track_member(msg)
    try:
        bot.send_message(
            msg.chat.id,
            "👋 *Welcome to GroupPay!*\n\n"
            "Split bills quickly and fairly with your group.\n\n"
            "Tap the button below or use /split to open the app.",
            parse_mode="Markdown",
            reply_markup=_make_keyboard(msg),
            **_thread_kwargs(msg),
        )
    except Exception as e:
        print(f"[CMD_START ERROR] chat_id={msg.chat.id}: {e}", flush=True)


@bot.message_handler(commands=["split"])
def cmd_split(msg):
    _log_message("CMD_SPLIT", msg)
    _track_member(msg)
    try:
        bot.send_message(
            msg.chat.id,
            "📢 *Let's split a bill!*\n\nTap below to open GroupPay:",
            parse_mode="Markdown",
            reply_markup=_make_keyboard(msg),
            **_thread_kwargs(msg),
        )
    except Exception as e:
        print(f"[CMD_SPLIT ERROR] chat_id={msg.chat.id}: {e}", flush=True)


@bot.message_handler(commands=["help"])
def cmd_help(msg):
    _log_message("CMD_HELP", msg)
    _track_member(msg)
    try:
        bot.send_message(
            msg.chat.id,
            "*GroupPay Commands*\n\n"
            "/start — Welcome & open the app\n"
            "/split — Start a new bill split\n"
            "/help — Show this help message",
            parse_mode="Markdown",
            **_thread_kwargs(msg),
        )
    except Exception as e:
        print(f"[CMD_HELP ERROR] chat_id={msg.chat.id}: {e}", flush=True)


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

def _auto_remind_loop():
    """Background loop: check for due auto-reminders every 5 minutes."""
    import time as _time
    while True:
        try:
            due = db.get_due_reminders()
            for session in due:
                chat_id = session.get("chat_id")
                if not chat_id:
                    continue
                cid = int(chat_id)
                tid_kwargs = {}
                if session.get("thread_id"):
                    tid_kwargs["message_thread_id"] = int(session["thread_id"])
                unpaid = [p for p in session["participants"] if p["status"] != "paid"]
                if not unpaid:
                    continue
                try:
                    names = "\n".join(
                        f"  • {_mention(p['name'], p.get('telegram_id'), cid)} — <b>${p['amount']}</b>"
                        for p in unpaid
                    )
                    payee_mention = _mention(session["payee"], chat_id=cid)
                    session_url = f"{WEBAPP_URL}?session={session['id']}"
                    kb = types.InlineKeyboardMarkup()
                    kb.add(types.InlineKeyboardButton("💰 View Split", url=session_url))
                    bot.send_message(
                        cid,
                        f"🔔 <b>Auto-Reminder</b>\n\n"
                        f"For <b>{session['event_name']}</b> organized by {payee_mention}:\n\n"
                        f"{names}\n\n"
                        f"Please pay soon! 🙏",
                        parse_mode="HTML",
                        reply_markup=kb,
                        **tid_kwargs,
                    )
                    print(f"[AUTO-REMIND] Sent for session {session['id']} to chat {cid} ({len(unpaid)} unpaid)")
                    # One-shot: clear reminder after sending
                    db.cancel_auto_remind(session["id"])
                except Exception as e:
                    print(f"[AUTO-REMIND ERROR] Session {session['id']}: {e}")
                    err_msg = str(e).lower()
                    if "upgraded to a supergroup" in err_msg or "chat not found" in err_msg:
                        print(f"[AUTO-REMIND] Cancelling reminder for session {session['id']} (stale chat)")
                        db.cancel_auto_remind(session["id"])
        except Exception as e:
            print(f"[AUTO-REMIND ERROR] Loop: {e}")
        _time.sleep(300)  # Check every 5 minutes


# ---------------------------------------------------------------------------
# Webhook endpoint — Telegram sends updates here instead of us polling
# ---------------------------------------------------------------------------

@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    """Receive Telegram updates via webhook."""
    if request.headers.get("content-type") == "application/json":
        update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
        bot.process_new_updates([update])
    return "", 200


def _setup_webhook():
    """Set Telegram webhook to point at our server."""
    webhook_url = f"{WEBAPP_URL}/webhook/{BOT_TOKEN}"
    import time

    last_error = None
    for attempt in range(1, 9):
        try:
            bot.remove_webhook()
            time.sleep(0.5)
            bot.set_webhook(url=webhook_url, drop_pending_updates=True)
            print(f"🔗 Webhook set: {webhook_url}")
            return
        except Exception as e:
            last_error = e
            wait = min(30, attempt * 5)
            print(f"[WEBHOOK] Setup attempt {attempt}/8 failed: {e}")
            if attempt < 8:
                print(f"[WEBHOOK] Retrying in {wait}s...")
                time.sleep(wait)

    raise last_error


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    db.init_db()
    print(f"🤖 GroupPay Bot running — Mini App URL: {WEBAPP_URL}")
    print("🌐 Flask API running on http://0.0.0.0:5000")

    # Set up webhook if WEBAPP_URL is configured, otherwise fall back to polling
    use_webhook = bool(WEBAPP_URL)

    # Run auto-reminder checker in background
    remind_thread = threading.Thread(target=_auto_remind_loop, daemon=True)
    remind_thread.start()

    if use_webhook:
        _setup_webhook()
        # Flask handles everything — webhook + API + static files
        app.run(host="0.0.0.0", port=5000, debug=False)
    else:
        # Fallback: polling mode (for local dev without tunnel)
        print("⚠️  No WEBAPP_URL — using polling mode (set WEBAPP_URL for webhook)")
        flask_thread = threading.Thread(
            target=lambda: app.run(host="0.0.0.0", port=5000, debug=False),
            daemon=True,
        )
        flask_thread.start()
        bot.infinity_polling(skip_pending=True)
