# final_bot.py  (compatible with python-telegram-bot==13.15)
import logging
import sqlite3
import json
import time
import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ParseMode
from telegram.ext import Updater, CommandHandler, MessageHandler, CallbackQueryHandler, Filters

# ---------------- CONFIG ----------------
BOT_TOKEN = "8014945735:AAFtydPfTWK6qQD5z9WKuUEKD-QWvvGEXCU"  # replace with os.getenv("BOT_TOKEN") if you prefer
ADMIN_ID = 8051564945

# default channels (initial values) - usernames kept here; UI hides them
DEFAULT_SPONSOR = "@jeremyupdates"
DEFAULT_PROMOTERS = ["@ffx_updates", "@SmartEarnOfficial", "@kingtupdate1", "@seyi_update"]
DEFAULT_PAYMENT = "@payment_channel001"

# display labels (Style 1 chosen)
DISPLAY_SPONSOR = "⭐ Sponsor Channel"
DISPLAY_PROMO_PREFIX = "📢 Promoter"
DISPLAY_PAYMENT = "💳 Payment Channel"
DISPLAY_JOIN_BUTTON = "✅ I Have Joined"

REFERRAL_REWARD = 30
MIN_WITHDRAW = 300
JOIN_CHECK_COOLDOWN = 8  # seconds
TASKS_FILE = "tasks.json"
DB_FILE = "bot.db"
# ----------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --------------- DB ---------------------
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
cur = conn.cursor()

# users table: id, balance, referrer
cur.execute("""CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY,
    balance REAL DEFAULT 0,
    referrer INTEGER
)""")

# proofs table for submitted proofs
cur.execute("""CREATE TABLE IF NOT EXISTS proofs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    task_id INTEGER,
    timestamp INTEGER
)""")

# channels table for must-join channels (order matters)
cur.execute("""CREATE TABLE IF NOT EXISTS must_join(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE, -- e.g. @channelname
    label TEXT -- display label shown on buttons
)""")

conn.commit()

# seed default channels if not present
def seed_default_channels():
    # sponsor entry as first
    cur.execute("SELECT COUNT(*) FROM must_join")
    if cur.fetchone()[0] == 0:
        # insert sponsor
        cur.execute("INSERT OR IGNORE INTO must_join(username,label) VALUES(?,?)", (DEFAULT_SPONSOR, DISPLAY_SPONSOR))
        # insert promoters
        for i, p in enumerate(DEFAULT_PROMOTERS, start=1):
            cur.execute("INSERT OR IGNORE INTO must_join(username,label) VALUES(?,?)", (p, f\"{DISPLAY_PROMO_PREFIX} {i}\"))
        # insert payment
        cur.execute("INSERT OR IGNORE INTO must_join(username,label) VALUES(?,?)", (DEFAULT_PAYMENT, DISPLAY_PAYMENT))
        conn.commit()

seed_default_channels()

# ---------------- tasks helpers ----------------
def load_tasks():
    if not os.path.exists(TASKS_FILE):
        with open(TASKS_FILE, "w") as f:
            json.dump([], f)
    with open(TASKS_FILE, "r") as f:
        return json.load(f)

def save_tasks(tasks):
    with open(TASKS_FILE, "w") as f:
        json.dump(tasks, f, indent=2)

# ---------------- utils ----------------
def user_exists(uid):
    cur.execute("SELECT 1 FROM users WHERE id=?", (uid,))
    return cur.fetchone() is not None

def add_user_db(uid, ref=None, username=None, first_name=None):
    if not user_exists(uid):
        cur.execute("INSERT INTO users(id,referrer) VALUES(?,?)", (uid, ref))
        conn.commit()
        # credit referrer
        if ref and user_exists(ref):
            try:
                cur.execute("UPDATE users SET balance = balance + ? WHERE id=?", (REFERRAL_REWARD, ref))
                conn.commit()
                try:
                    updater.bot.send_message(ref, f\"🎉 You earned ₦{REFERRAL_REWARD} for inviting @{username or first_name or ref}!\")
                except Exception:
                    pass
            except Exception as e:
                logger.info(\"Referral credit failed: %s\", e)
        # announce new user
        try:
            display = f\"@{username}\" if username else (first_name or str(uid))
            text = f\"👤 *New User Joined*\\n\\nUsername: {display}\\nReferral: {'✅ Yes' if ref else '❌ No'}\"
            updater.bot.send_message(DEFAULT_PAYMENT, text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.info(\"Could not announce new user: %s\", e)

def ensure_user(uid):
    if not user_exists(uid):
        add_user_db(uid, None)

def get_display_name(uid):
    try:
        user = updater.bot.get_chat(uid)
        if hasattr(user, 'username') and user.username:
            return f\"@{user.username}\"
        if hasattr(user, 'first_name') and user.first_name:
            return user.first_name
    except Exception:
        pass
    return str(uid)

# ---------------- must-join helpers ----------------
def get_must_join_list():
    cur.execute("SELECT username,label FROM must_join ORDER BY id")
    return cur.fetchall()  # list of tuples (username, label)

def add_must_join_channel(username, label=None):
    if not username.startswith("@"):
        username = "@" + username
    if not label:
        # set label depending on position
        cur.execute("SELECT COUNT(*) FROM must_join")
        n = cur.fetchone()[0]
        label = f\"{DISPLAY_PROMO_PREFIX} {n}\" if n>=1 else DISPLAY_SPONSOR
    cur.execute("INSERT OR IGNORE INTO must_join(username,label) VALUES(?,?)", (username, label))
    conn.commit()

def remove_must_join_channel(username):
    if not username.startswith("@"):
        username = "@" + username
    cur.execute("DELETE FROM must_join WHERE username=?", (username,))
    conn.commit()

def build_join_keyboard():
    rows = []
    mj = get_must_join_list()
    for username, label in mj:
        link = f\"https://t.me/{username.lstrip('@')}\"
        rows.append([InlineKeyboardButton(label, url=link)])
    rows.append([InlineKeyboardButton(DISPLAY_JOIN_BUTTON, callback_data=\"check_join\")])
    return InlineKeyboardMarkup(rows)

def check_missing_roles(uid):
    missing = []
    mj = get_must_join_list()
    for username, label in mj:
        try:
            member = updater.bot.get_chat_member(username, uid)
            if member.status not in (\"member\", \"creator\", \"administrator\"):
                missing.append(label)
        except Exception as e:
            # treat as missing if bot cannot check
            logger.info(\"check_missing_roles: couldn't check %s for %s: %s\", username, uid, e)
            missing.append(label)
    return missing

# in-memory cooldowns
join_cooldowns = {}

# ---------------- Bot Handlers ----------------
def cmd_start(update, context):
    user = update.effective_user
    uid = user.id
    ref = None
    if context.args:
        try:
            ref = int(context.args[0])
        except:
            ref = None
    add_user_db(uid, ref, username=user.username, first_name=user.first_name)
    missing = check_missing_roles(uid)
    if missing:
        text = (\"🚨 *Before continuing, please join our required channels:*\\n\\n\"
                \"Tap each button below to open the channel and join. When finished, press *I Have Joined*.\")
        update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=build_join_keyboard())
        return
    show_main_menu(update, context)

def show_main_menu(update, context):
    keyboard = [
        [KeyboardButton(\"🧾 Earn Tasks\"), KeyboardButton(\"💰 Balance\")],
        [KeyboardButton(\"👥 Referrals\"), KeyboardButton(\"💵 Withdraw\")]
    ]
    if update.effective_user.id == ADMIN_ID:
        keyboard.append([KeyboardButton(\"⚙ Admin Panel\")])
    update.message.reply_text(\"🏠 *Dashboard*\", parse_mode=ParseMode.MARKDOWN,
                              reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

def cb_check_join(update, context):
    query = update.callback_query
    uid = query.from_user.id
    now = time.time()
    last = join_cooldowns.get(uid, 0)
    if now - last < JOIN_CHECK_COOLDOWN:
        remaining = int(JOIN_CHECK_COOLDOWN - (now - last))
        return query.answer(f\"⏳ Please wait {remaining}s before checking again.\", show_alert=True)
    join_cooldowns[uid] = now
    missing = check_missing_roles(uid)
    if not missing:
        try:
            query.message.delete()
        except:
            pass
        try:
            query.answer(\"✅ Verified — you joined all required channels!\")
        except:
            pass
        show_main_menu(update, context)
    else:
        text = \"❌ You have not joined all required channels yet.\\n\\nMissing:\\n\" + \"\\n\".join(f\"• {r}\" for r in missing)
        # keyboard only for missing ones
        buttons = []
        mj = get_must_join_list()
        for username, label in mj:
            if label in missing:
                buttons.append([InlineKeyboardButton(label, url=f\"https://t.me/{username.lstrip('@')}\")])
        buttons.append([InlineKeyboardButton(DISPLAY_JOIN_BUTTON, callback_data=\"check_join\")])
        try:
            query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(buttons))
        except:
            query.answer(\"❌ You have not joined all required channels.\", show_alert=True)

def msg_text(update, context):
    text = update.message.text or \"\"
    uid = update.effective_user.id
    ensure_user(uid)
    missing = check_missing_roles(uid)
    if missing:
        update.message.reply_text(\"🚨 You must join required channels first.\", reply_markup=build_join_keyboard())
        return

    if text == \"💰 Balance\" or text.lower().startswith(\"balance\"):
        cur.execute(\"SELECT balance FROM users WHERE id=?\", (uid,))
        row = cur.fetchone()
        bal = row[0] if row else 0
        update.message.reply_text(f\"💳 Your Balance: *₦{bal}*\", parse_mode=ParseMode.MARKDOWN)
        return

    if text == \"👥 Referrals\" or text.lower().startswith(\"referrals\") :
        cur.execute(\"SELECT COUNT(*) FROM users WHERE referrer=?\", (uid,))
        count = cur.fetchone()[0]
        link = f\"https://t.me/{BOT_USERNAME}?start={uid}\"
        cur.execute(\"SELECT balance FROM users WHERE id=?\", (uid,))
        row = cur.fetchone()
        bal = row[0] if row else 0
        update.message.reply_text(f\"👤 Referrals: {count}\\n👥 Invite Count: {count}\\n\\n🔗 Referral Link:\\n{link}\\n\\n💰 Balance: ₦{bal}\", parse_mode=ParseMode.MARKDOWN)
        return

    if text == \"🧾 Earn Tasks\" or text.lower().startswith(\"tasks\") :
        tasks = load_tasks()
        if not tasks:
            update.message.reply_text(\"No tasks available at the moment.\")
            return
        for t in tasks:
            tid = t.get(\"id\")
            title = t.get(\"title\")
            price = t.get(\"price\")
            link = t.get(\"link\")
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(\"✅ Submit Proof\", callback_data=f\"proof_{tid}\")]])
            update.message.reply_text(f\"📌 *{title}*\\n💰 Reward: ₦{price}\\n🔗 {link}\", parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        return

    if text == \"💵 Withdraw\" or text.lower().startswith(\"withdraw\") :
        update.message.reply_text(f\"Enter amount to withdraw (min ₦{MIN_WITHDRAW}):\")
        context.user_data[\"withdraw\"] = True
        return

    # withdraw amount handling
    if text.isdigit() and context.user_data.get(\"withdraw\"):
        amt = int(text)
        cur.execute(\"SELECT balance FROM users WHERE id=?\", (uid,))
        row = cur.fetchone()
        bal = row[0] if row else 0
        if amt < MIN_WITHDRAW or amt > bal:
            update.message.reply_text(\"❌ Invalid amount.\")
            return
        context.user_data[\"withdraw_amount\"] = amt
        update.message.reply_text(\"Enter Bank Name:\")
        context.user_data[\"withdraw_step\"] = 1
        return

    if \"withdraw_step\" in context.user_data:
        step = context.user_data[\"withdraw_step\"]
        if step == 1:
            context.user_data[\"bank\"] = text
            update.message.reply_text(\"Enter Account Number:\")
            context.user_data[\"withdraw_step\"] = 2
            return
        elif step == 2:
            context.user_data[\"acct\"] = text
            update.message.reply_text(\"Enter Account Name:\")
            context.user_data[\"withdraw_step\"] = 3
            return
        elif step == 3:
            context.user_data[\"acct_name\"] = text
            process_withdraw_request(update, context)
            return

    if text == \"⚙ Admin Panel\" and uid == ADMIN_ID:
        cmd_adminpanel(update, context)
        return

    update.message.reply_text(\"Use the menu. Send /start to show join page.\")

def process_withdraw_request(update, context):
    uid = update.effective_user.id
    amt = context.user_data.get(\"withdraw_amount\")
    bank = context.user_data.get(\"bank\")
    acct = context.user_data.get(\"acct\")
    name = context.user_data.get(\"acct_name\")

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(\"✅ Approve\", callback_data=f\"approve_{uid}_{amt}\"),
         InlineKeyboardButton(\"❌ Reject\", callback_data=f\"reject_{uid}\")]
    ])

    display = get_display_name(uid)
    text = f\"💵 *Withdrawal Request*\\n\\nUser: {display}\\nAmount: ₦{amt}\\nBank: {bank}\\nAccount: {acct}\\nName: {name}\"
    try:
        # send to payment channel (username hidden in UI because the channel itself is not shown to users)
        cur.execute(\"SELECT username FROM must_join ORDER BY id DESC LIMIT 1\")
        updater.bot.send_message(DEFAULT_PAYMENT, text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    except Exception as e:
        logger.info(\"Failed to send withdraw to payment channel: %s\", e)
        update.message.reply_text(\"Could not notify payment channel. Contact admin.\")
        return

    update.message.reply_text(\"✅ Withdrawal requested. You will be paid in 20-30 minutes.\")
    # cleanup
    for k in [\"withdraw\",\"withdraw_amount\",\"withdraw_step\",\"bank\",\"acct\",\"acct_name\"]:
        context.user_data.pop(k, None)

# ---------------- Callback router ----------------
def callback_router(update, context):
    query = update.callback_query
    data = query.data or \"\"
    uid = query.from_user.id

    if data == \"check_join\" or data == \"check_joined\":
        return cb_check_join(update, context)

    if data.startswith(\"approve_\") or data.startswith(\"approve-\") or data.startswith(\"ap_\"):
        if uid != ADMIN_ID:
            return query.answer(\"Only admin can approve.\", show_alert=True)
        # support multiple formats
        try:
            parts = data.split(\"_\") if \"_\" in data else data.split(\"-\")
            user_id = int(parts[1])
            amt = float(parts[2])
            cur.execute(\"UPDATE users SET balance = balance - ? WHERE id=?\", (amt, user_id))
            conn.commit()
            try:
                updater.bot.send_message(user_id, f\"✅ Your withdrawal of ₦{amt} has been approved and paid.\")
            except Exception:
                pass
            try:
                query.message.edit_reply_markup(None)
            except:
                pass
            query.answer(\"Approved\")
        except Exception as e:
            logger.info(\"approve error: %s\", e)
            query.answer(\"Error approving\", show_alert=True)
        return

    if data.startswith(\"reject_\") or data.startswith(\"rj_\") or data.startswith(\"reject-\"):
        if uid != ADMIN_ID:
            return query.answer(\"Only admin can reject.\", show_alert=True)
        try:
            parts = data.split(\"_\") if \"_\" in data else data.split(\"-\")
            user_id = int(parts[1])
            try:
                updater.bot.send_message(user_id, \"❌ Your withdrawal was rejected. Please contact admin.\")
            except Exception:
                pass
            try:
                query.message.edit_reply_markup(None)
            except:
                pass
            query.answer(\"Rejected\")
        except Exception as e:
            logger.info(\"reject error: %s\", e)
            query.answer(\"Error rejecting\", show_alert=True)
        return

    if data.startswith(\"proof_\"):
        try:
            task_id = int(data.split(\"_\",1)[1])
            context.user_data[\"proof_task\"] = task_id
            query.message.reply_text(\"📸 Send screenshot/photo of the completed task now.\")
            query.answer()
        except Exception as e:
            logger.info(\"proof callback error: %s\", e)
            query.answer(\"Error\", show_alert=True)
        return

    if data.startswith(\"admin_\"):
        # simple admin inline menu actions (explain commands)
        if uid != ADMIN_ID:
            return query.answer(\"Not authorized\", show_alert=True)
        action = data.split(\"admin_\",1)[1]
        if action == \"addtask\":
            query.message.reply_text(\"Send command: /addtask title|price|link\")
        elif action == \"removetask\":
            query.message.reply_text(\"Send command: /removetask <id>\")
        elif action == \"listtasks\":
            tasks = load_tasks()
            if not tasks:
                query.message.reply_text(\"No tasks available.\")
            else:
                text = \"📋 Tasks:\\n\"
                for t in tasks:
                    text += f\"ID:{t.get('id')} - {t.get('title')} - ₦{t.get('price')} - {t.get('link')}\\n\"
                query.message.reply_text(text)
        elif action == \"addbal\":
            query.message.reply_text(\"Send command: /addbal <user_id> <amount>\")
        elif action == \"broadcast\":
            query.message.reply_text(\"Send command: /broadcast Your message here\")
        elif action == \"setref\":
            query.message.reply_text(\"Send command: /setref <amount>\")
        elif action == \"addchannel\":
            query.message.reply_text(\"Send command: /addchannel @channelusername [Label optional]\")
        elif action == \"rmchannel\":
            query.message.reply_text(\"Send command: /rmchannel @channelusername\")
        elif action == \"close\":
            try:
                query.message.delete()
            except:
                pass
        return

# ---------------- Photo proof handler ----------------
def photo_handler(update, context):
    uid = update.effective_user.id
    if \"proof_task\" not in context.user_data:
        update.message.reply_text(\"First click Submit Proof on a task.\")
        return
    task_id = context.user_data[\"proof_task\"]
    cur.execute(\"SELECT 1 FROM proofs WHERE user_id=? AND task_id=?\", (uid, task_id))
    if cur.fetchone():
        update.message.reply_text(\"❌ You already submitted this task.\")
        context.user_data.pop(\"proof_task\", None)
        return
    ts = int(time.time())
    cur.execute(\"INSERT INTO proofs(user_id, task_id, timestamp) VALUES(?,?,?)\", (uid, task_id, ts))
    conn.commit()
    tasks = load_tasks()
    price = 0
    for t in tasks:
        if t.get(\"id\") == task_id:
            price = float(t.get(\"price\", 0))
            break
    # credit immediately (you can change to wait for admin approval)
    cur.execute(\"UPDATE users SET balance = balance + ? WHERE id=?\", (price, uid))
    conn.commit()
    try:
        caption = f\"✅ Task Proof Submitted\\nUser: {get_display_name(uid)}\\nTask ID: {task_id}\\nReward: ₦{price}\"
        updater.bot.send_photo(DEFAULT_PAYMENT, update.message.photo[-1].file_id, caption=caption)
    except Exception as e:
        logger.info(\"Failed to forward proof: %s\", e)
    update.message.reply_text(f\"✅ Proof received. You earned ₦{price}. Admin will verify in the payments channel.\")
    context.user_data.pop(\"proof_task\", None)

# ---------------- Admin text commands ----------------
def cmd_adminpanel(update, context):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        update.message.reply_text(\"❌ You are not authorized.\")
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(\"➕ Add Task\", callback_data=\"admin_addtask\"), InlineKeyboardButton(\"🗑 Remove Task\", callback_data=\"admin_removetask\")],
        [InlineKeyboardButton(\"📋 List Tasks\", callback_data=\"admin_listtasks\"), InlineKeyboardButton(\"💰 Add Balance\", callback_data=\"admin_addbal\")],
        [InlineKeyboardButton(\"📢 Broadcast\", callback_data=\"admin_broadcast\"), InlineKeyboardButton(\"🎁 Set Referral\", callback_data=\"admin_setref\")],
        [InlineKeyboardButton(\"🔧 Add Channel\", callback_data=\"admin_addchannel\"), InlineKeyboardButton(\"❌ Remove Channel\", callback_data=\"admin_rmchannel\")],
        [InlineKeyboardButton(\"🔙 Close\", callback_data=\"admin_close\")]
    ])
    update.message.reply_text(\"⚙️ *ADMIN PANEL*\", parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

def admin_cmds(update, context):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        return
    text = update.message.text.strip()
    # addtask: /addtask title|price|link
    if text.startswith(\"/addtask \"):
        try:
            payload = text.split(\" \",1)[1]
            title, price, link = payload.split(\"|\")
            tasks = load_tasks()
            new_id = 1 if not tasks else max(t.get('id',0) for t in tasks) + 1
            tasks.append({\"id\": new_id, \"title\": title.strip(), \"price\": float(price), \"link\": link.strip()})
            save_tasks(tasks)
            update.message.reply_text(f\"✅ Task added with ID {new_id}.\")
        except Exception as e:
            logger.info(\"addtask err: %s\", e)
            update.message.reply_text(\"Usage: /addtask title|price|link\")
        return

    if text.startswith(\"/removetask \"):
        try:
            tid = int(text.split(\" \",1)[1])
            tasks = load_tasks()
            new_tasks = [t for t in tasks if t.get('id') != tid]
            if len(new_tasks) == len(tasks):
                update.message.reply_text(\"Task ID not found.\")
                return
            save_tasks(new_tasks)
            update.message.reply_text(f\"✅ Removed task ID {tid}.\")
        except Exception as e:
            logger.info(\"removetask err: %s\", e)
            update.message.reply_text(\"Usage: /removetask <id>\")
        return

    # /addbal <user_id> <amount>
    if text.startswith(\"/addbal \"):
        try:
            _, user_str, amt_str = text.split(\" \",2)
            uid_target = int(user_str); amt = float(amt_str)
            cur.execute(\"UPDATE users SET balance = balance + ? WHERE id=?\", (amt, uid_target))
            conn.commit()
            update.message.reply_text(\"✅ Balance added.\")
        except Exception as e:
            logger.info(\"addbal err: %s\", e)
            update.message.reply_text(\"Usage: /addbal <user_id> <amount>\")
        return

    # /broadcast message
    if text.startswith(\"/broadcast \"):
        try:
            msg = text.split(\" \",1)[1]
            cur.execute(\"SELECT id FROM users\")
            rows = cur.fetchall()
            sent = 0
            for (u,) in rows:
                try:
                    updater.bot.send_message(u, f\"📣 Broadcast:\\n\\n{msg}\")
                    sent += 1
                except:
                    pass
            update.message.reply_text(f\"Broadcast sent to {sent} users.\")
        except Exception as e:
            logger.info(\"broadcast err: %s\", e)
            update.message.reply_text(\"Usage: /broadcast message\")
        return

    # /setref amount
    if text.startswith(\"/setref \"):
        try:
            amt = float(text.split(\" \",1)[1])
            global REFERRAL_REWARD
            REFERRAL_REWARD = amt
            update.message.reply_text(f\"✅ Referral reward set to ₦{amt}\")
        except Exception as e:
            logger.info(\"setref err: %s\", e)
            update.message.reply_text(\"Usage: /setref amount\")
        return

    # /addchannel @username [Optional Label]
    if text.startswith(\"/addchannel \"):
        try:
            parts = text.split(\" \",2)
            username = parts[1].strip()
            label = None
            if len(parts) == 3:
                label = parts[2].strip()
            add_must_join_channel(username, label)
            update.message.reply_text(f\"✅ Added must-join channel {username}\")
        except Exception as e:
            logger.info(\"addchannel err: %s\", e)
            update.message.reply_text(\"Usage: /addchannel @channelusername [Label optional]\")
        return

    # /rmchannel @username
    if text.startswith(\"/rmchannel \"):
        try:
            username = text.split(\" \",1)[1].strip()
            remove_must_join_channel(username)
            update.message.reply_text(f\"✅ Removed must-join channel {username}\")
        except Exception as e:
            logger.info(\"rmchannel err: %s\", e)
            update.message.reply_text(\"Usage: /rmchannel @channelusername\")
        return

    update.message.reply_text(\"Admin commands: /addtask /removetask /addbal /broadcast /setref /addchannel /rmchannel\")


# --------------- Startup ----------------
updater = Updater(BOT_TOKEN, use_context=True)
dp = updater.dispatcher

# handlers
dp.add_handler(CommandHandler(\"start\", cmd_start))
dp.add_handler(CommandHandler(\"adminpanel\", cmd_adminpanel))
dp.add_handler(CallbackQueryHandler(callback_router))
dp.add_handler(MessageHandler(Filters.text & Filters.user(user_id=ADMIN_ID), admin_cmds))
dp.add_handler(MessageHandler(Filters.text & ~Filters.command, msg_text))
dp.add_handler(MessageHandler(Filters.photo, photo_handler))

if __name__ == '__main__':
    print(\"✅ Final bot.py ready — running (v13.15)...\")
    updater.start_polling()
    updater.idle()
