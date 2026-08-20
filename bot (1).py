#!/usr/bin/env python3
"""
╔══════════════════════════════════════╗
║    OVERLORD - Join Accepter Bot      ║
║  Auto-Approve | Welcome | Broadcast  ║
╚══════════════════════════════════════╝
"""

import logging
import sqlite3
import asyncio
import os
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ChatJoinRequestHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from telegram.error import TelegramError

# ─────────────────────────────────────────
#  CONFIGURATION  (edit these)
# ─────────────────────────────────────────
BOT_TOKEN  = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_IDS  = [int(x) for x in os.getenv("ADMIN_IDS", "123456789").split(",")]

# ─────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────
#  DATABASE
# ─────────────────────────────────────────
DB = "overlord.db"

def init_db():
    with sqlite3.connect(DB) as conn:
        c = conn.cursor()
        c.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id    INTEGER PRIMARY KEY,
                username   TEXT,
                first_name TEXT,
                last_name  TEXT,
                added_on   TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS chats (
                chat_id    INTEGER PRIMARY KEY,
                title      TEXT,
                chat_type  TEXT,
                added_on   TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        defaults = [
            ("auto_approve", "true"),
            ("welcome_msg",
             "✅ *Welcome, {name}!*\n\n"
             "Your join request to *{chat}* has been approved! 🎉\n"
             "Enjoy your stay!"),
            ("welcome_buttons", ""),   # JSON string of button rows
        ]
        c.executemany("INSERT OR IGNORE INTO settings VALUES (?,?)", defaults)
        conn.commit()

# DB helpers
def db_get(key: str) -> str:
    with sqlite3.connect(DB) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else ""

def db_set(key: str, value: str):
    with sqlite3.connect(DB) as conn:
        conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, value))

def upsert_user(user_id, username, first_name, last_name):
    with sqlite3.connect(DB) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO users VALUES (?,?,?,?,datetime('now'))",
            (user_id, username, first_name, last_name),
        )

def upsert_chat(chat_id, title, chat_type):
    with sqlite3.connect(DB) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO chats VALUES (?,?,?,datetime('now'))",
            (chat_id, title, chat_type),
        )

def all_user_ids():
    with sqlite3.connect(DB) as conn:
        return [r[0] for r in conn.execute("SELECT user_id FROM users")]

def user_count():
    with sqlite3.connect(DB) as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

def all_chats():
    with sqlite3.connect(DB) as conn:
        return conn.execute("SELECT chat_id, title FROM chats").fetchall()

# ─────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Stats",        callback_data="stats"),
         InlineKeyboardButton("🔗 Linked Chats", callback_data="chats")],
        [InlineKeyboardButton("✏️ Welcome Msg",  callback_data="set_welcome"),
         InlineKeyboardButton("🔘 Welcome Btns", callback_data="set_wbuttons")],
        [InlineKeyboardButton("📢 Broadcast",    callback_data="broadcast"),
         InlineKeyboardButton("🔄 Toggle Auto",  callback_data="toggle_auto")],
    ])

def parse_buttons(raw: str) -> list[list[dict]]:
    """
    Format per row: Text1|URL1 :: Text2|URL2
    Rows separated by newlines.
    """
    rows = []
    for line in raw.strip().splitlines():
        row = []
        for pair in line.split("::"):
            parts = pair.strip().split("|", 1)
            if len(parts) == 2:
                row.append({"text": parts[0].strip(), "url": parts[1].strip()})
        if row:
            rows.append(row)
    return rows

def build_markup(rows: list[list[dict]]) -> InlineKeyboardMarkup | None:
    if not rows:
        return None
    kb = [[InlineKeyboardButton(b["text"], url=b["url"]) for b in row] for row in rows]
    return InlineKeyboardMarkup(kb)

# ─────────────────────────────────────────
#  /start
# ─────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_user(user.id, user.username, user.first_name, user.last_name)

    if is_admin(user.id):
        auto = db_get("auto_approve")
        await update.message.reply_text(
            f"👑 *OVERLORD Join Bot*\n\n"
            f"Hello, *{user.first_name}!* Admin panel below.\n\n"
            f"🔄 Auto-Approve: *{'ON ✅' if auto == 'true' else 'OFF ❌'}*\n\n"
            f"Add me as **Admin** to your channel/group with\n"
            f"➡️ *Add Members* permission enabled.",
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )
    else:
        await update.message.reply_text(
            "👑 *OVERLORD Join Accepter Bot*\n\n"
            "⚡ Best Channel + Group Request Management Bot\n"
            "✅ Instantly accept new join requests\n"
            "👑 Process pending requests in bulk\n\n"
            "🛡️ Auto Approve • Ultra-fast • 100% Free!",
            parse_mode="Markdown",
        )

# ─────────────────────────────────────────
#  JOIN REQUEST HANDLER
# ─────────────────────────────────────────
async def on_join_request(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    req  = update.chat_join_request
    user = req.from_user
    chat = req.chat

    if db_get("auto_approve") != "true":
        return

    try:
        await req.approve()
    except TelegramError as e:
        logger.error(f"Approve error: {e}")
        return

    upsert_user(user.id, user.username, user.first_name, user.last_name)
    upsert_chat(chat.id, chat.title, chat.type)

    # Build welcome text
    template = db_get("welcome_msg")
    text = template.format(
        name     = user.first_name,
        username = f"@{user.username}" if user.username else user.first_name,
        chat     = chat.title,
    )

    # Build welcome buttons
    raw_btns = db_get("welcome_buttons")
    markup   = build_markup(parse_buttons(raw_btns)) if raw_btns else None

    try:
        await ctx.bot.send_message(
            chat_id    = user.id,
            text       = text,
            parse_mode = "Markdown",
            reply_markup = markup,
        )
    except TelegramError:
        pass  # User may have blocked the bot

    logger.info(f"✅ Approved {user.first_name} ({user.id}) → {chat.title}")

# ─────────────────────────────────────────
#  CALLBACK QUERY ROUTER
# ─────────────────────────────────────────
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not is_admin(q.from_user.id):
        await q.answer("❌ Admins only!", show_alert=True)
        return

    data = q.data

    # ── Stats ──────────────────────────────
    if data == "stats":
        auto = db_get("auto_approve")
        chats = all_chats()
        await q.edit_message_text(
            f"📊 *BOT STATISTICS*\n\n"
            f"👥 Total Users : `{user_count()}`\n"
            f"📢 Linked Chats: `{len(chats)}`\n"
            f"🔄 Auto-Approve: `{'ON ✅' if auto == 'true' else 'OFF ❌'}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back")]]),
        )

    # ── Linked chats ───────────────────────
    elif data == "chats":
        chats = all_chats()
        lines = "\n".join(f"• {t} (`{cid}`)" for cid, t in chats) if chats else "No chats linked yet."
        await q.edit_message_text(
            f"🔗 *Linked Channels / Groups*\n\n{lines}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back")]]),
        )

    # ── Toggle auto-approve ────────────────
    elif data == "toggle_auto":
        new = "false" if db_get("auto_approve") == "true" else "true"
        db_set("auto_approve", new)
        label = "ON ✅" if new == "true" else "OFF ❌"
        await q.edit_message_text(
            f"🔄 Auto-Approve is now *{label}*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back")]]),
        )

    # ── Set welcome message ────────────────
    elif data == "set_welcome":
        ctx.user_data["waiting"] = "welcome_msg"
        await q.edit_message_text(
            "✏️ *Set Welcome Message*\n\n"
            "Send your new welcome message now.\n\n"
            "📌 *Variables:*\n"
            "`{name}` → User's first name\n"
            "`{username}` → @username\n"
            "`{chat}` → Channel / Group name\n\n"
            "Supports *Markdown* formatting.\n"
            "Send /cancel to abort.",
            parse_mode="Markdown",
        )

    # ── Set welcome buttons ────────────────
    elif data == "set_wbuttons":
        ctx.user_data["waiting"] = "welcome_buttons"
        await q.edit_message_text(
            "🔘 *Set Welcome Inline Buttons*\n\n"
            "Each line = one button row.\n"
            "Format per button: `Text | URL`\n"
            "Multiple buttons in same row: `T1|U1 :: T2|U2`\n\n"
            "Example:\n"
            "`Join Channel | https://t.me/yourchannel`\n"
            "`Website | https://example.com :: Help | https://t.me/bot`\n\n"
            "Send empty message or /skip to remove buttons.\n"
            "Send /cancel to abort.",
            parse_mode="Markdown",
        )

    # ── Broadcast ──────────────────────────
    elif data == "broadcast":
        ctx.user_data["waiting"]        = "broadcast_content"
        ctx.user_data["bc_data"]        = {}
        ctx.user_data["bc_buttons"]     = []
        await q.edit_message_text(
            "📢 *Broadcast*\n\n"
            "Send the content to broadcast:\n"
            "• Text message\n"
            "• Photo (with optional caption)\n"
            "• Video (with optional caption)\n\n"
            "Send /cancel to abort.",
            parse_mode="Markdown",
        )

    # ── Add broadcast buttons ──────────────
    elif data == "bc_add_btn":
        ctx.user_data["waiting"] = "bc_buttons"
        await q.edit_message_text(
            "🔘 *Add Inline Buttons to Broadcast*\n\n"
            "Each line = one button row.\n"
            "Format: `Text | URL`\n"
            "Multiple per row: `T1|U1 :: T2|U2`\n\n"
            "Send /done when finished.",
            parse_mode="Markdown",
        )

    # ── Send broadcast immediately ─────────
    elif data == "bc_send":
        await q.edit_message_text("📤 Broadcasting…")
        await _do_broadcast(q.message, ctx)

    # ── Back ───────────────────────────────
    elif data == "back":
        auto = db_get("auto_approve")
        await q.edit_message_text(
            f"👑 *OVERLORD Join Bot* — Admin Panel\n\n"
            f"🔄 Auto-Approve: *{'ON ✅' if auto == 'true' else 'OFF ❌'}*",
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )

# ─────────────────────────────────────────
#  MESSAGE HANDLER (admin input)
# ─────────────────────────────────────────
async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    waiting = ctx.user_data.get("waiting", "")
    msg     = update.message

    # cancel
    if msg.text in ("/cancel", "/skip") and waiting:
        ctx.user_data.clear()
        await msg.reply_text("❌ Cancelled.", reply_markup=main_keyboard())
        return

    # ── Welcome message ────────────────────
    if waiting == "welcome_msg" and msg.text:
        db_set("welcome_msg", msg.text)
        ctx.user_data.clear()
        await msg.reply_text(
            f"✅ Welcome message saved!\n\n*Preview:*\n{msg.text}",
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )

    # ── Welcome buttons ────────────────────
    elif waiting == "welcome_buttons":
        raw = msg.text.strip() if msg.text else ""
        db_set("welcome_buttons", raw)
        ctx.user_data.clear()
        if raw:
            rows   = parse_buttons(raw)
            markup = build_markup(rows)
            await msg.reply_text(
                f"✅ Welcome buttons saved! ({len(rows)} row(s))\n\n"
                "Preview of buttons below 👇",
                reply_markup=markup,
            )
        else:
            await msg.reply_text("✅ Welcome buttons cleared.", reply_markup=main_keyboard())

    # ── Broadcast content ──────────────────
    elif waiting == "broadcast_content":
        if msg.photo:
            ctx.user_data["bc_data"] = {
                "type": "photo",
                "file_id": msg.photo[-1].file_id,
                "caption": msg.caption or "",
            }
        elif msg.video:
            ctx.user_data["bc_data"] = {
                "type": "video",
                "file_id": msg.video.file_id,
                "caption": msg.caption or "",
            }
        elif msg.text:
            ctx.user_data["bc_data"] = {"type": "text", "text": msg.text}
        else:
            await msg.reply_text("⚠️ Unsupported type. Send text, photo, or video.")
            return

        ctx.user_data["waiting"] = "bc_ready"
        await msg.reply_text(
            "✅ Content saved!\n\nNow add inline buttons (optional):",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Add Buttons", callback_data="bc_add_btn"),
                 InlineKeyboardButton("🚀 Send Now",    callback_data="bc_send")],
            ]),
        )

    # ── Broadcast buttons ──────────────────
    elif waiting == "bc_buttons":
        if msg.text == "/done":
            ctx.user_data["waiting"] = "bc_ready"
            rows   = ctx.user_data.get("bc_buttons", [])
            markup = build_markup(rows)
            preview_txt = f"✅ {len(rows)} button row(s) saved.\n\nReady to broadcast!"
            await msg.reply_text(
                preview_txt,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🚀 Send Broadcast", callback_data="bc_send")],
                ]),
            )
            return

        rows_raw = parse_buttons(msg.text or "")
        if rows_raw:
            ctx.user_data.setdefault("bc_buttons", []).extend(rows_raw)
            await msg.reply_text(
                f"✅ Added {len(rows_raw)} row(s). Send more or /done.",
            )
        else:
            await msg.reply_text("❌ Invalid format! Use: `Text | URL`", parse_mode="Markdown")

# ─────────────────────────────────────────
#  PHOTO / VIDEO HANDLER (for broadcast)
# ─────────────────────────────────────────
async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if ctx.user_data.get("waiting") == "broadcast_content":
        await on_message(update, ctx)

async def on_video(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if ctx.user_data.get("waiting") == "broadcast_content":
        await on_message(update, ctx)

# ─────────────────────────────────────────
#  BROADCAST EXECUTOR
# ─────────────────────────────────────────
async def _do_broadcast(status_msg, ctx: ContextTypes.DEFAULT_TYPE):
    bc_data  = ctx.user_data.get("bc_data", {})
    bc_btns  = ctx.user_data.get("bc_buttons", [])
    markup   = build_markup(bc_btns)
    users    = all_user_ids()
    ok = fail = 0

    for uid in users:
        try:
            t = bc_data.get("type")
            if t == "text":
                await ctx.bot.send_message(
                    uid, bc_data["text"],
                    parse_mode="Markdown", reply_markup=markup,
                )
            elif t == "photo":
                await ctx.bot.send_photo(
                    uid, bc_data["file_id"],
                    caption=bc_data.get("caption", ""),
                    parse_mode="Markdown", reply_markup=markup,
                )
            elif t == "video":
                await ctx.bot.send_video(
                    uid, bc_data["file_id"],
                    caption=bc_data.get("caption", ""),
                    parse_mode="Markdown", reply_markup=markup,
                )
            ok += 1
            await asyncio.sleep(0.04)   # stay under Telegram rate limit
        except TelegramError:
            fail += 1

    ctx.user_data.clear()
    try:
        await status_msg.edit_text(
            f"✅ *Broadcast Complete!*\n\n"
            f"✅ Delivered : `{ok}`\n"
            f"❌ Failed    : `{fail}`\n"
            f"👥 Total     : `{ok + fail}`",
            parse_mode="Markdown",
        )
    except Exception:
        pass

# ─────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────
def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))

    # Join requests (the core feature)
    app.add_handler(ChatJoinRequestHandler(on_join_request))

    # Callback buttons
    app.add_handler(CallbackQueryHandler(on_callback))

    # Media handlers (broadcast)
    app.add_handler(MessageHandler(
        filters.PHOTO & filters.ChatType.PRIVATE, on_photo))
    app.add_handler(MessageHandler(
        filters.VIDEO & filters.ChatType.PRIVATE, on_video))

    # Text messages (admin input)
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND, on_message))

    logger.info("🤖 OVERLORD Bot is running…")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
