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
    if not user_exists(user_id):
        cur.execute("INSERT INTO users(id, referrer) VALUES(?,?)", (user_id, referrer))
        conn.commit()


def check_join(update):
    user_id = update.effective_user.id
    for ch in MUST_JOIN_CHANNELS:
        try:
            member = updater.bot.get_chat_member(ch, user_id)
            if member.status not in ["member", "administrator", "creator"]:
                return False
        except:
            return False
    return True


def start(update, context):
    user_id = update.effective_user.id

    ref = None
    if context.args:
        try:
            ref = int(context.args[0])
        except:
            pass

    add_user(user_id, ref)

    if not check_join(update):
        btn = [[InlineKeyboardButton("✅ Join Channels", url="https://t.me/jeremyupdates")]]
        update.message.reply_text("🚨 You must join all required channels before using the bot.", reply_markup=InlineKeyboardMarkup(btn))
        return

    menu(update, context)


def menu(update, context):
    keyboard = [
        ["🧾 Earn Tasks", "💰 Balance"],
        ["👥 Referrals", "📊 Leaderboard"],
        ["💵 Withdraw"]
    ]
    if update.effective_user.id == ADMIN_ID:
        keyboard.append(["⚙ Admin Panel"])
    update.message.reply_text("🏠 *Dashboard*", parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))


def text_handler(update, context):
    msg = update.message.text
    user_id = update.effective_user.id

    if msg == "💰 Balance":
        cur.execute("SELECT balance FROM users WHERE id=?", (user_id,))
        bal = cur.fetchone()[0]
        update.message.reply_text(f"💳 Your Balance: *₦{bal}*", parse_mode="Markdown")

    elif msg == "👥 Referrals":
        cur.execute("SELECT COUNT(*) FROM users WHERE referrer=?", (user_id,))
        count = cur.fetchone()[0]
        link = f"https://t.me/{context.bot.username}?start={user_id}"
        update.message.reply_text(f"👤 Referrals: {count}\n\n🔗 Referral Link:\n{link}")

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
        bal = cur.fetchone()[0]
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

            context.bot.send_message(PAYMENT_CHANNEL, f"💵 *Withdrawal Request*\n\nUser: `{user_id}`\nAmount: ₦{amt}\nBank: {bank}\nAccount: {acct}\nName: {acct_name}", parse_mode="Markdown")

            update.message.reply_text("✅ Withdrawal requested. You will be paid in 20-30 minutes.")
            del context.user_data["withdraw_step"]
            del context.user_data["withdraw"]


def button(update, context):
    query = update.callback_query
    data = query.data

    if data.startswith("proof_"):
        task_id = data.split("_")[1]
        context.user_data["proof_task"] = task_id
        query.message.reply_text("📸 Send screenshot of task completed.")


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
    price = cur.fetchone()[0]

    cur.execute("UPDATE users SET balance=balance+? WHERE id=?", (price, user_id))
    conn.commit()

    context.bot.send_photo(PAYMENT_CHANNEL, update.message.photo[-1].file_id,
    caption=f"✅ Task Proof Submitted\nUser: `{user_id}`\nReward: ₦{price}", parse_mode="Markdown")

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

dp.add_handler(CommandHandler("start", start))
dp.add_handler(MessageHandler(Filters.text, text_handler))
dp.add_handler(CommandHandler(["addtask", "addbal"], admin))
dp.add_handler(CallbackQueryHandler(button))
dp.add_handler(MessageHandler(Filters.photo, save_photo))

updater.start_polling()
print("Bot Running ✅")
