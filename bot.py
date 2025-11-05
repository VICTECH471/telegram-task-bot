# bot.py (v20+ compatible)
import os
import logging
import sqlite3
import time
from typing import List, Tuple

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ParseMode,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN") or "8014945735:AAFtydPfTWK6qQD5z9WKuUEKD-QWvvGEXCU"
ADMIN_ID = int(os.getenv("ADMIN_ID") or 8051564945)

BOT_USERNAME = "CashgiveawayV1Bot"

# real channel usernames (kept here but not shown to users)
SPONSOR_CHANNEL = "@jeremyupdates"
PROMOTER_CHANNELS = [
    "@SmartEarnOfficial",
    "@seyi_update",
    "@kingtupdate1",
    "@ffx_updates",
]
PAYMENT_CHANNEL = "@payment_channel001"

# display labels (users see these instead of real usernames)
DISPLAY_SPONSOR = "⭐ Sponsor Channel"
DISPLAY_PROMO_PREFIX = "📢 Promo Channel"
DISPLAY_PAYMENT = "💳 Payment Channel"

REFERRAL_REWARD = 30
MIN_WITHDRAW = 300
JOIN_CHECK_COOLDOWN = 10  # seconds
# -----------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- DATABASE ----------------
DB_FILE = "bot.db"
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
cur = conn.cursor()

cur.execute(
    """CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY,
        balance REAL DEFAULT 0,
        referrer INTEGER
    )"""
)
cur.execute(
    """CREATE TABLE IF NOT EXISTS tasks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        price REAL,
        link TEXT
    )"""
)
cur.execute(
    """CREATE TABLE IF NOT EXISTS proofs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        task_id INTEGER,
        timestamp INTEGER
    )"""
)
conn.commit()


# ---------- helper DB funcs ----------
def user_exists(uid: int) -> bool:
    cur.execute("SELECT 1 FROM users WHERE id=?", (uid,))
    return cur.fetchone() is not None


def add_user_db(uid: int, ref: int | None):
    if not user_exists(uid):
        cur.execute("INSERT INTO users(id, referrer) VALUES(?,?)", (uid, ref))
        conn.commit()
        # credit referrer if present
        if ref and user_exists(ref):
            cur.execute("UPDATE users SET balance = balance + ? WHERE id=?", (REFERRAL_REWARD, ref))
            conn.commit()
            try:
                application.bot.send_message(
                    chat_id=int(ref),
                    text=f"🎉 You earned ₦{REFERRAL_REWARD} for inviting a new user!"
                )
            except Exception:
                logger.info("Could not notify referrer %s", ref)


def ensure_user_record(uid: int):
    if not user_exists(uid):
        add_user_db(uid, None)


# ---------- channel role helpers ----------
def build_roles_and_channels() -> Tuple[List[str], List[str]]:
    """Return parallel lists: role labels and real usernames."""
    channels = [SPONSOR_CHANNEL] + PROMOTER_CHANNELS + [PAYMENT_CHANNEL]
    roles = [DISPLAY_SPONSOR] + [f"{DISPLAY_PROMO_PREFIX} {i+1}" for i in range(len(PROMOTER_CHANNELS))] + [DISPLAY_PAYMENT]
    return roles, channels


def build_join_keyboard() -> InlineKeyboardMarkup:
    roles, channels = build_roles_and_channels()
    buttons = []
    for role, ch in zip(roles, channels):
        link = f"https://t.me/{ch.lstrip('@')}"
        buttons.append([InlineKeyboardButton(role, url=link)])
    buttons.append([InlineKeyboardButton("✅ I Have Joined", callback_data="check_joined")])
    return InlineKeyboardMarkup(buttons)


async def check_missing_roles_for_user(application: Application, uid: int) -> List[str]:
    roles, channels = build_roles_and_channels()
    missing = []
    for role, ch in zip(roles, channels):
        try:
            member = await application.bot.get_chat_member(chat_id=ch, user_id=uid)
            if member.status in ("left", "kicked"):
                missing.append(role)
        except Exception as e:
            # cannot access (bot not in channel or private) -> treat as missing
            logger.info("Check failed for %s, user %s: %s", ch, uid, e)
            missing.append(role)
    return missing


# ---------- in-memory cooldown ----------
join_cooldowns: dict[int, float] = {}


# ---------- utilities ----------
def get_display_for_user_sync(uid: int) -> str:
    """Synchronous fallback to create a display string from DB or id."""
    try:
        cur.execute("SELECT id FROM users WHERE id=?", (uid,))
        if cur.fetchone():
            # we will try to get username via bot if needed in async handlers
            return str(uid)
    except Exception:
        pass
    return str(uid)


# ---------- Handlers ----------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    args = context.args
    ref = None
    if args:
        try:
            ref = int(args[0])
        except Exception:
            ref = None

    # store user and credit referrer if new
    add_user_db(uid, ref)

    # announce new user to payment channel (async)
    try:
        uname = f"@{user.username}" if user.username else (user.first_name or str(uid))
        await context.bot.send_message(
            chat_id=PAYMENT_CHANNEL,
            text=f"👤 *New User Joined*\n\nUsername: {uname}\nReferral: {'✅ Yes' if ref else '❌ No'}",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.info("Could not announce new user to payment channel: %s", e)

    # check joins
    missing = await check_missing_roles_for_user(context.application, uid)
    if missing:
        text = (
            "🚨 *Before continuing, please join our required channels:*\n\n"
            "You will see buttons below that open each channel. Join them, then press *I Have Joined*.\n\n"
            "If the bot is not added to a channel (private / not member), it cannot verify automatically — contact admin."
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=build_join_keyboard())
        return

    # all joined -> show menu
    await show_main_menu(update, context)


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # support both message and callback contexts
    keyboard = [
        [KeyboardButton("🧾 Earn Tasks"), KeyboardButton("💰 Balance")],
        [KeyboardButton("👥 Referrals"), KeyboardButton("💵 Withdraw")]
    ]
    if update.effective_user.id == ADMIN_ID:
        keyboard.append([KeyboardButton("⚙ Admin Panel")])
    text = "🏠 *Dashboard*"
    # if callback query present, edit or send accordingly
    # easiest is to reply a new message:
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode=ParseMode.MARKDOWN,
                                   reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))


async def cb_check_joined(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    now = time.time()
    last = join_cooldowns.get(uid, 0)
    if now - last < JOIN_CHECK_COOLDOWN:
        remaining = int(JOIN_CHECK_COOLDOWN - (now - last))
        await query.answer(f"⏳ Please wait {remaining}s before checking again.", show_alert=True)
        return
    join_cooldowns[uid] = now

    missing = await check_missing_roles_for_user(context.application, uid)
    if not missing:
        try:
            await query.message.delete()
        except Exception:
            pass
        await query.answer("✅ Verified — you joined all required channels!")
        # show menu
        await show_main_menu(update, context)
    else:
        text = "❌ You have not joined all required channels yet.\n\nMissing:\n"
        text += "\n".join(f"• {r}" for r in missing)
        text += "\n\nOpen the missing channels from the buttons and press I Have Joined again."
        # build keyboard only for missing
        roles, channels = build_roles_and_channels()
        buttons = []
        for role, ch in zip(roles, channels):
            if role in missing:
                buttons.append([InlineKeyboardButton(role, url=f"https://t.me/{ch.lstrip('@')}")])
        buttons.append([InlineKeyboardButton("✅ I Have Joined", callback_data="check_joined")])
        await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(buttons))
        await query.answer()


async def show_join_page_if_needed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # helper on any message to enforce join requirement
    uid = update.effective_user.id
    missing = await check_missing_roles_for_user(context.application, uid)
    if missing:
        await update.message.reply_text("🚨 You must join required channels first.", reply_markup=build_join_keyboard())
        return True
    return False


async def msg_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    uid = update.effective_user.id

    # ensure user record
    ensure_user_record(uid)

    # enforce join
    missing = await check_missing_roles_for_user(context.application, uid)
    if missing:
        await update.message.reply_text("🚨 You must join required channels first.", reply_markup=build_join_keyboard())
        return

    if text == "💰 Balance":
        cur.execute("SELECT balance FROM users WHERE id=?", (uid,))
        row = cur.fetchone()
        bal = row[0] if row else 0
        await update.message.reply_text(f"💳 Your Balance: *₦{bal}*", parse_mode=ParseMode.MARKDOWN)
        return

    if text == "👥 Referrals":
        cur.execute("SELECT COUNT(*) FROM users WHERE referrer=?", (uid,))
        count = cur.fetchone()[0]
        link = f"https://t.me/{BOT_USERNAME}?start={uid}"
        await update.message.reply_text(f"👤 Referrals: {count}\n\n🔗 Referral Link:\n{link}")
        return

    if text == "🧾 Earn Tasks":
        cur.execute("SELECT id, title, price, link FROM tasks")
        tasks = cur.fetchall()
        if not tasks:
            await update.message.reply_text("No tasks available now.")
            return
        for t in tasks:
            btn = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Submit Proof", callback_data=f"proof_{t[0]}")]])
            await update.message.reply_text(f"📌 *{t[1]}*\n💰 Reward: ₦{t[2]}\n🔗 {t[3]}",
                                            parse_mode=ParseMode.MARKDOWN, reply_markup=btn)
        return

    if text == "💵 Withdraw":
        await update.message.reply_text(f"Enter amount to withdraw (min ₦{MIN_WITHDRAW}):")
        context.user_data["wd"] = True
        return

    if text == "⚙ Admin Panel":
        await update.message.reply_text("Use /adminpanel (admin only).")
        return

    # admin message commands handled elsewhere; but allow /adminpanel call handling by command handler

    # if withdrawing steps
    if "wd" in context.user_data and text.isdigit():
        amt = int(text)
        cur.execute("SELECT balance FROM users WHERE id=?", (uid,))
        row = cur.fetchone()
        bal = row[0] if row else 0
        if amt < MIN_WITHDRAW or amt > bal:
            await update.message.reply_text("❌ Invalid amount.")
            return
        context.user_data["amount"] = amt
        await update.message.reply_text("Enter Bank Name:")
        context.user_data["step"] = 1
        return

    if "step" in context.user_data:
        step = context.user_data["step"]
        if step == 1:
            context.user_data["bank"] = text
            context.user_data["step"] = 2
            await update.message.reply_text("Enter Account Number:")
            return
        if step == 2:
            context.user_data["acct"] = text
            context.user_data["step"] = 3
            await update.message.reply_text("Enter Account Name:")
            return
        if step == 3:
            context.user_data["acct_name"] = text
            await process_withdraw_request(update, context)
            return

    # fallback
    await update.message.reply_text("Use the menu or /start. ")

# ---------- withdraw flow ----------
async def process_withdraw_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    amt = context.user_data.get("amount")
    bank = context.user_data.get("bank")
    acct = context.user_data.get("acct")
    name = context.user_data.get("acct_name")

    # post to payment channel with approve reject
    text = (
        f"💵 *Withdrawal Request*\n\n"
        f"User: `{await get_display_for_user(uid, context)}`\n"
        f"Amount: ₦{amt}\n"
        f"Bank: {bank}\n"
        f"Account: {acct}\n"
        f"Name: {name}"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"approve_{uid}_{amt}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"reject_{uid}")
    ]])
    try:
        await context.bot.send_message(chat_id=PAYMENT_CHANNEL, text=text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    except Exception as e:
        logger.info("Could not send withdraw to payment channel: %s", e)
        await update.message.reply_text("Could not send withdraw request to payment channel. Contact admin.")
        return

    await update.message.reply_text("✅ Withdrawal submitted! You will be paid in 20-30 minutes.")
    context.user_data.pop("step", None)
    context.user_data.pop("wd", None)
    context.user_data.pop("amount", None)
    context.user_data.pop("bank", None)
    context.user_data.pop("acct", None)
    context.user_data.pop("acct_name", None)


async def get_display_for_user(uid: int, context: ContextTypes.DEFAULT_TYPE) -> str:
    try:
        user = await context.bot.get_chat(uid)
        if user.username:
            return f"@{user.username}"
        if user.first_name:
            return f"{user.first_name}"
    except Exception:
        pass
    return str(uid)


# ---------- proof/photo handler ----------
async def cb_proof_submit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("proof_"):
        try:
            task_id = int(data.split("_", 1)[1])
            context.user_data["proof_task"] = task_id
            await query.message.reply_text("📸 Send screenshot/photo of the task now.")
        except Exception as e:
            logger.info("proof callback error: %s", e)
            await query.message.reply_text("Error processing proof request.")


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if "proof_task" not in context.user_data:
        await update.message.reply_text("First click Submit Proof on a task.")
        return
    task_id = context.user_data["proof_task"]
    # ensure not already submitted
    cur.execute("SELECT 1 FROM proofs WHERE user_id=? AND task_id=?", (uid, task_id))
    if cur.fetchone():
        await update.message.reply_text("❌ You already submitted this task.")
        context.user_data.pop("proof_task", None)
        return

    # store proof record
    ts = int(time.time())
    cur.execute("INSERT INTO proofs(user_id, task_id, timestamp) VALUES(?,?,?)", (uid, task_id, ts))
    conn.commit()

    # credit instantly (if desired) — currently instant credit
    cur.execute("SELECT price FROM tasks WHERE id=?", (task_id,))
    row = cur.fetchone()
    price = row[0] if row else 0
    cur.execute("UPDATE users SET balance = balance + ? WHERE id=?", (price, uid))
    conn.commit()

    # forward proof to payment channel
    try:
        caption = f"✅ Task Proof Submitted\nUser: {await get_display_for_user(uid, context)}\nTask ID: {task_id}\nReward: ₦{price}"
        file_id = update.message.photo[-1].file_id if update.message.photo else None
        if file_id:
            await context.bot.send_photo(chat_id=PAYMENT_CHANNEL, photo=file_id, caption=caption)
        else:
            await context.bot.send_message(chat_id=PAYMENT_CHANNEL, text=caption)
    except Exception as e:
        logger.info("Could not forward proof: %s", e)

    await update.message.reply_text(f"✅ Proof received. You earned ₦{price}.")
    context.user_data.pop("proof_task", None)


# ---------- admin panel & admin commands ----------
async def cmd_adminpanel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized to use admin panel.")
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Task", callback_data="admin_addtask"),
         InlineKeyboardButton("💰 Add Balance", callback_data="admin_addbal")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
         InlineKeyboardButton("🎁 Set Referral", callback_data="admin_setref")],
        [InlineKeyboardButton("🔧 Manage Channels", callback_data="admin_channels"),
         InlineKeyboardButton("🏧 Withdrawals", callback_data="admin_withdraws")],
        [InlineKeyboardButton("🔙 Close", callback_data="admin_close")]
    ])
    await update.message.reply_text("⚙️ *ADMIN PANEL*\nChoose an action:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def admin_inline_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if uid != ADMIN_ID:
        await query.answer("Not authorized", show_alert=True)
        return
    d = query.data

    if d == "admin_addtask":
        await query.message.reply_text("To add a task use:\n/addtask title|price|link")
    elif d == "admin_addbal":
        await query.message.reply_text("To add balance use:\n/addbal <user_id> <amount>")
    elif d == "admin_broadcast":
        await query.message.reply_text("To broadcast use:\n/broadcast Your message here")
    elif d == "admin_setref":
        await query.message.reply_text("To set referral reward:\n/setref <amount>")
    elif d == "admin_channels":
        await query.message.reply_text("Use /addchannel @username and /rmchannel @username to manage channels")
    elif d == "admin_withdraws":
        await query.message.reply_text("Withdraw requests arrive in the payment channel; approve/reject there.")
    elif d == "admin_close":
        try:
            await query.message.delete()
        except Exception:
            pass


async def admin_text_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        return
    text = update.message.text.strip()
    # /addtask title|price|link
    if text.startswith("/addtask "):
        try:
            _, payload = text.split(" ", 1)
            title, price, link = payload.split("|")
            cur.execute("INSERT INTO tasks(title, price, link) VALUES(?,?,?)", (title.strip(), float(price), link.strip()))
            conn.commit()
            await update.message.reply_text("✅ Task added.")
        except Exception as e:
            logger.info("addtask err: %s", e)
            await update.message.reply_text("Usage: /addtask title|price|link")
    elif text.startswith("/addbal "):
        try:
            _, uid_str, amt = text.split(" ", 2)
            cur.execute("UPDATE users SET balance = balance + ? WHERE id=?", (float(amt), int(uid_str)))
            conn.commit()
            await update.message.reply_text("✅ Balance added.")
        except Exception as e:
            logger.info("addbal err: %s", e)
            await update.message.reply_text("Usage: /addbal user_id amount")
    elif text.startswith("/broadcast "):
        try:
            msg = text.split(" ", 1)[1]
            cur.execute("SELECT id FROM users")
            rows = 
