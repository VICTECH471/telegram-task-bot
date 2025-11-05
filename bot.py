import logging
from telegram.ext import *
from telegram import *
import sqlite3
import datetime

# ---------------- SETTINGS ---------------- #

BOT_TOKEN = "8014945735:AAFtydPfTWK6qQD5z9WKuUEKD-QWvvGEXCU"

ADMIN_ID = 8051564945   # your admin id
PAYMENT_CHANNEL = "@payment_channel001"

MUST_JOIN_CHANNELS = [
    "@jeremyupdates",
    "@SmartEarnOfficial",
    "@seyi_update",
    "@kingtupdate1",
    "@payment_channel001",
    "@ffx_updates"
]

REFERRAL_REWARD = 30  # ₦30 per invite

# ------------------------------------------- #

logging.basicConfig(level=logging.INFO)
conn = sqlite3.connect("bot.db", check_same_thread=False)
cur = conn.cursor()

cur.execute("""CREATE TABLE IF NOT EXISTS users(
id INTEGER PRIMARY KEY,
balance INTEGER DEFAULT 0,
referrer INTEGER)""")

cur.execute("""CREATE TABLE IF NOT EXISTS tasks(
id INTEGER PRIMARY KEY AUTOINCREMENT,
title TEXT,
price INTEGER,
link TEXT)""")

cur.execute("""CREATE TABLE IF NOT EXISTS proofs(
user_id INTEGER,
task_id INTEGER)""")

conn.commit()


def user_exists(user_id):
    cur.execute("SELECT 1 FROM users WHERE id=?", (user_id,))
    return cur.fetchone() is not None


def add_user(user_id, referrer=None):
    # return True if new user, False if existing
    if not user_exists(user_id):
        cur.execute("INSERT INTO users(id, referrer) VALUES(?,?)", (user_id, referrer))
        conn.commit()
        # 🎁 Auto Referral Bonus ₦30
        if referrer:
            try:
                cur.execute("UPDATE users SET balance = balance + ? WHERE id=?", (REFERRAL_REWARD, referrer))
                conn.commit()
                # notify inviter
                try:
                    updater.bot.send_message(referrer, f"🎉 You earned ₦{REFERRAL_REWARD} for inviting a new user!")
                except:
                    pass
            except Exception as e:
                logging.info("Referral credit failed: %s", e)
        return True
    return False


# NEW: robust check function that returns missing channels list
def check_join_user(user_id):
    missing = []
    for ch in MUST_JOIN_CHANNELS:
        try:
            member = updater.bot.get_chat_member(chat_id=ch, user_id=user_id)
            if member.status in ("left", "kicked"):
                missing.append(ch)
        except Exception as e:
            # If bot can't access channel info (bot not in channel or channel private),
            # consider it missing to force admin/manual handling.
            logging.info("check_join_user: couldn't check %s for %s: %s", ch, user_id, e)
            missing.append(ch)
    return missing  # empty list = all joined


def start(update, context):
    # Handles /start and referral param
    user_id = update.effective_user.id
    is_new = False

    # --- REFERRAL HANDLING ---
    ref = None
    if context.args:
        try:
            ref = int(context.args[0])
        except:
            ref = None

    # Add user and detect if new
    is_new = add_user(user_id, ref)

    # If new user came from referral → reward inviter handled inside add_user

    # Now check membership
    missing = check_join_user(user_id)
    if missing:
        # Build join buttons: one row per channel (URL) and an "I Joined" button
        buttons = []
        for ch in MUST_JOIN_CHANNELS:
            # use channel username to create public t.me link
            link = f"https://t.me/{ch.lstrip('@')}"
            buttons.append([InlineKeyboardButton(ch, url=link)])
        # add final "I Joined" button
        buttons.append([InlineKeyboardButton("✅ I Joined", callback_data="check_joined")])

        update.message.reply_text(
            "🚨 You must join the following required channels before using the bot.\n\n"
            "Click each channel below to open it and join. When you finish, press *I Joined*.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    # If already joined, show menu
    menu(update, context)


def menu(update, context):
    # Works with both Message updates and CallbackQuery updates
    user_obj = update.effective_user
    user_id = user_obj.id
    keyboard = [
        ["🧾 Earn Tasks", "💰 Balance"],
        ["👥 Referrals", "📊 Leaderboard"],
        ["💵 Withdraw"]
    ]
    # Add admin panel for admin
    if user_id == ADMIN_ID:
        keyboard.append(["⚙ Admin Panel"])
    # If update is a callback query, use edit_message_text or send a new message accordingly
    if update.callback_query:
        try:
            update.callback_query.message.edit_text("🏠 *Dashboard*", parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
        except:
            update.callback_query.message.reply_text("🏠 *Dashboard*", parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    else:
        update.message.reply_text("🏠 *Dashboard*", parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))


def text_handler(update, context):
    msg = update.message.text
    user_id = update.effective_user.id

    if msg == "💰 Balance":
        cur.execute("SELECT balance FROM users WHERE id=?", (user_id,))
        row = cur.fetchone()
        bal = row[0] if row else 0
        update.message.reply_text(f"💳 Your Balance: *₦{bal}*", parse_mode="Markdown")

    elif msg == "👥 Referrals":
        cur.execute("SELECT COUNT(*) FROM users WHERE referrer=?", (user_id,))
        count = cur.fetchone()[0]
        link = f"https://t.me/{context.bot.username}?start={user_id}"
        # show invite count and link
        cur.execute("SELECT balance FROM users WHERE id=?", (user_id,))
        row = cur.fetchone()
        bal = row[0] if row else 0
        update.message.reply_text(f"👤 Referrals: {count}\n👥 Invite Count: {count}\n\n🔗 Referral Link:\n{link}\n\n💰 Balance: ₦{bal}", parse_mode="Markdown")

    elif msg == "📊 Leaderboard":
        cur.execute("SELECT id, balance FROM users ORDER BY balance DESC LIMIT 10")
        rows = cur.fetchall()
        text = "🏆 *Top Earners:*\n\n"
        for i, r in enumerate(rows):
            text += f"{i+1}. `{r[0]}` - ₦{r[1]}\n"
        update.message.reply_text(text, parse_mode="Markdown")

    elif msg == "🧾 Earn Tasks":
        cur.execute("SELECT id, title, price, link FROM tasks")
        tasks = cur.fetchall()
        if not tasks:
            update.message.reply_text("No tasks available now.")
            return
        for t in tasks:
            btn = [[InlineKeyboardButton("✅ Submit Proof", callback_data=f"proof_{t[0]}")]]
            update.message.reply_text(f"📌 *{t[1]}*\n💰 Reward: ₦{t[2]}\n🔗 {t[3]}", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btn))

    elif msg == "💵 Withdraw":
        update.message.reply_text("Enter amount to withdraw (minimum ₦300):")
        context.user_data["withdraw"] = True

    elif msg == "⚙ Admin Panel" and user_id == ADMIN_ID:
        update.message.reply_text("Admin Commands:\n\n/addtask title|price|link\n/addbal user_id amount")

    elif msg.isdigit() and "withdraw" in context.user_data:
        amt = int(msg)
        cur.execute("SELECT balance FROM users WHERE id=?", (user_id,))
        row = cur.fetchone()
        bal = row[0] if row else 0
        if amt < 300 or amt > bal:
            update.message.reply_text("❌ Invalid amount.")
        else:
            context.user_data["withdraw_amount"] = amt
            update.message.reply_text("Enter Bank Name:")
            context.user_data["withdraw_step"] = 1

    elif "withdraw_step" in context.user_data:
        step = context.user_data["withdraw_step"]
        if step == 1:
            context.user_data["bank"] = msg
            update.message.reply_text("Enter Account Number:")
            context.user_data["withdraw_step"] = 2
        elif step == 2:
            context.user_data["acct"] = msg
            update.message.reply_text("Enter Account Name:")
            context.user_data["withdraw_step"] = 3
        elif step == 3:
            context.user_data["acct_name"] = msg
            amt = context.user_data["withdraw_amount"]
            bank = context.user_data["bank"]
            acct = context.user_data["acct"]
            acct_name = context.user_data["acct_name"]

            # ✅ Approve / Reject Buttons
            btns = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user_id}_{amt}"),
                 InlineKeyboardButton("❌ Reject", callback_data=f"reject_{user_id}")]
            ])

            context.bot.send_message(PAYMENT_CHANNEL,
                f"💵 *Withdrawal Request*\n\nUser: `{user_id}`\nAmount: ₦{amt}\nBank: {bank}\nAccount: {acct}\nName: {acct_name}",
                reply_markup=btns, parse_mode="Markdown")

            update.message.reply_text("✅ Withdrawal requested. You will be paid in 20-30 minutes.")
            del context.user_data["withdraw_step"]
            del context.user_data["withdraw"]


def check_joined_cb(update, context):
    # callback for "I Joined" button
    query = update.callback_query
    user_id = query.from_user.id
    query.answer()  # acknowledge

    missing = check_join_user(user_id)
    if not missing:
        # All good — show menu/dashboard
        try:
            query.message.reply_text("✅ You have joined all channels. Welcome!")
        except:
            pass
        # Call menu with the original update object so menu handles it
        menu(update, context)
    else:
        # show which channels are missing
        text = "❌ You have not joined these channels yet:\n\n" + "\n".join(missing) + "\n\nClick the channel links above and join, then press *I Joined* again."
        buttons = []
        for ch in missing:
            buttons.append([InlineKeyboardButton(ch, url=f"https://t.me/{ch.lstrip('@')}")])
        buttons.append([InlineKeyboardButton("✅ I Joined", callback_data="check_joined")])
        try:
            query.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))
        except:
            pass


def button(update, context):
    query = update.callback_query
    data = query.data

    if data == "check_joined":
        # route to check_joined handler - keep here if CallbackQueryHandler ordering doesn't match
        return check_joined_cb(update, context)

    if data.startswith("proof_"):
        task_id = data.split("_")[1]
        context.user_data["proof_task"] = task_id
        query.message.reply_text("📸 Send screenshot of task completed.")

    elif data.startswith("approve_"):
        _, uid, amt = data.split("_")
        try:
            amt = int(amt)
        except:
            amt = float(amt)
        cur.execute("UPDATE users SET balance = balance - ? WHERE id=?", (amt, uid))
        conn.commit()
        try:
            context.bot.send_message(int(uid), f"✅ Your withdrawal of ₦{amt} has been approved and paid.")
        except Exception as e:
            logging.info("Could not notify user on approval: %s", e)
        try:
            query.message.edit_reply_markup(None)
        except:
            pass

    elif data.startswith("reject_"):
        _, uid = data.split("_")
        try:
            context.bot.send_message(int(uid), "❌ Your withdrawal was rejected. Please contact admin.")
        except Exception as e:
            logging.info("Could not notify user on rejection: %s", e)
        try:
            query.message.edit_reply_markup(None)
        except:
            pass


def save_photo(update, context):
    user_id = update.effective_user.id
    if "proof_task" not in context.user_data:
        return
    task_id = context.user_data["proof_task"]

    cur.execute("SELECT 1 FROM proofs WHERE user_id=? AND task_id=?", (user_id, task_id))
    if cur.fetchone():
        update.message.reply_text("❌ You already submitted this task.")
        return

    cur.execute("INSERT INTO proofs(user_id, task_id) VALUES(?,?)", (user_id, task_id))
    conn.commit()

    cur.execute("SELECT price FROM tasks WHERE id=?", (task_id,))
    row = cur.fetchone()
    price = row[0] if row else 0

    cur.execute("UPDATE users SET balance=balance+? WHERE id=?", (price, user_id))
    conn.commit()

    try:
        context.bot.send_photo(PAYMENT_CHANNEL, update.message.photo[-1].file_id,
        caption=f"✅ Task Proof Submitted\nUser: `{user_id}`\nReward: ₦{price}", parse_mode="Markdown")
    except Exception as e:
        logging.info("Failed to forward proof: %s", e)

    update.message.reply_text(f"✅ Task Approved! You earned ₦{price}.")
    del context.user_data["proof_task"]


def admin(update, context):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return
    txt = update.message.text

    if txt.startswith("/addtask "):
        _, d = txt.split(" ", 1)
        title, price, link = d.split("|")
        cur.execute("INSERT INTO tasks(title, price, link) VALUES(?,?,?)", (title, price, link))
        conn.commit()
        update.message.reply_text("✅ Task added.")

    elif txt.startswith("/addbal "):
        _, uid, amt = txt.split(" ", 2)
        cur.execute("UPDATE users SET balance=balance+? WHERE id=?", (amt, uid))
        conn.commit()
        update.message.reply_text("✅ Balance added.")


updater = Updater(BOT_TOKEN, use_context=True)
dp = updater.dispatcher

# Handlers
dp.add_handler(CommandHandler("start", start))
dp.add_handler(MessageHandler(Filters.text, text_handler))
dp.add_handler(CommandHandler(["addtask", "addbal"], admin))

# CallbackQuery handlers:
# Put specific handler for check_joined first so it is matched before the generic button handler
dp.add_handler(CallbackQueryHandler(check_joined_cb, pattern="^check_joined$"))
dp.add_handler(CallbackQueryHandler(button))

dp.add_handler(MessageHandler(Filters.photo, save_photo))

updater.start_polling()
print("Bot Running ✅")
