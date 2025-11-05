import logging
import sqlite3
from telegram import *
from telegram.ext import *
import datetime

# ---------------- SETTINGS ---------------- #
BOT_TOKEN = "8014945735:AAFtydPfTWK6qQD5z9WKuUEKD-QWvvGEXCU"
ADMIN_ID = 8051564945

BOT_USERNAME = "CashgiveawayV1Bot"

SPONSOR_CHANNEL = "@jeremyupdates"
PROMOTER_CHANNELS = [
    "@SmartEarnOfficial",
    "@seyi_update",
    "@kingtupdate1",
    "@ffx_updates"
]

PAYMENT_CHANNEL = "@payment_channel001"

REFERRAL_REWARD = 30
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


def user_exists(uid):
    cur.execute("SELECT 1 FROM users WHERE id=?", (uid,))
    return cur.fetchone() is not None


def add_user(uid, ref=None):
    if not user_exists(uid):
        cur.execute("INSERT INTO users(id, referrer) VALUES(?,?)", (uid, ref))
        conn.commit()

        if ref:
            cur.execute("UPDATE users SET balance = balance + ? WHERE id=?", (REFERRAL_REWARD, ref))
            conn.commit()

        # announce new user to payment channel
        text = f"👤 *New User Joined*\n\nUsername: @{uid}\nReferral: {'✅ Yes' if ref else '❌ No'}"
        updater.bot.send_message(PAYMENT_CHANNEL, text, parse_mode="Markdown")


def check_join(uid):
    channels = [SPONSOR_CHANNEL] + PROMOTER_CHANNELS + [PAYMENT_CHANNEL]
    for ch in channels:
        try:
            member = updater.bot.get_chat_member(ch, uid)
            if member.status not in ["member", "administrator", "creator"]:
                return False
        except:
            return False
    return True


def start(update, context):
    uid = update.effective_user.id

    ref = None
    if context.args:
        try:
            ref = int(context.args[0])
        except:
            pass

    add_user(uid, ref)

    if not check_join(uid):
        join_menu(update)
    else:
        main_menu(update)


def join_menu(update):
    text = (
        "🚨 *Before continuing, please join our required channels:*\n\n"
        "⭐ Sponsor Channel\n"
        "📢 Promoter Channels\n"
        "💳 Payment Channel\n\n"
        "After joining, tap the button below:"
    )
    btn = InlineKeyboardMarkup([[InlineKeyboardButton("✅ I Have Joined", callback_data="check_join")]])
    update.message.reply_text(text, parse_mode="Markdown", reply_markup=btn)


def main_menu(update):
    keyboard = [
        ["🧾 Earn Tasks", "💰 Balance"],
        ["👥 Referrals", "💵 Withdraw"]
    ]
    if update.effective_user.id == ADMIN_ID:
        keyboard.append(["⚙ Admin Panel"])
    update.message.reply_text("🏠 *Dashboard*", parse_mode="Markdown",
                              reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))


def text_handler(update, context):
    msg = update.message.text
    uid = update.effective_user.id

    if not check_join(uid):
        join_menu(update)
        return

    if msg == "💰 Balance":
        cur.execute("SELECT balance FROM users WHERE id=?", (uid,))
        bal = cur.fetchone()[0]
        update.message.reply_text(f"💳 Your Balance: *₦{bal}*", parse_mode="Markdown")

    elif msg == "👥 Referrals":
        cur.execute("SELECT COUNT(*) FROM users WHERE referrer=?", (uid,))
        count = cur.fetchone()[0]
        link = f"https://t.me/{BOT_USERNAME}?start={uid}"
        update.message.reply_text(f"👤 Referrals: {count}\n\n🔗 Referral Link:\n{link}")

    elif msg == "🧾 Earn Tasks":
        cur.execute("SELECT id, title, price, link FROM tasks")
        tasks = cur.fetchall()
        if not tasks:
            update.message.reply_text("No tasks available now.")
            return
        for t in tasks:
            btn = [[InlineKeyboardButton("✅ Submit Proof", callback_data=f"proof_{t[0]}")]]
            update.message.reply_text(f"📌 *{t[1]}*\n💰 Reward: ₦{t[2]}\n🔗 {t[3]}",
                                      parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btn))

    elif msg == "💵 Withdraw":
        update.message.reply_text("Enter amount to withdraw (min ₦300):")
        context.user_data["wd"] = True

    elif msg == "⚙ Admin Panel" and uid == ADMIN_ID:
        admin_panel(update)


    elif msg.isdigit() and "wd" in context.user_data:
        amt = int(msg)
        cur.execute("SELECT balance FROM users WHERE id=?", (uid,))
        bal = cur.fetchone()[0]
        if amt < 300 or amt > bal:
            update.message.reply_text("❌ Invalid amount.")
        else:
            context.user_data["amount"] = amt
            update.message.reply_text("Enter Bank Name:")
            context.user_data["step"] = 1

    elif "step" in context.user_data:
        step = context.user_data["step"]
        if step == 1:
            context.user_data["bank"] = msg
            update.message.reply_text("Enter Account Number:")
            context.user_data["step"] = 2
        elif step == 2:
            context.user_data["acct"] = msg
            update.message.reply_text("Enter Account Name:")
            context.user_data["step"] = 3
        elif step == 3:
            context.user_data["acct_name"] = msg
            withdraw_request(update, context)


def withdraw_request(update, context):
    uid = update.effective_user.id
    amt = context.user_data["amount"]
    bank = context.user_data["bank"]
    acct = context.user_data["acct"]
    name = context.user_data["acct_name"]

    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Approve", callback_data=f"approve_{uid}_{amt}"),
         InlineKeyboardButton("❌ Reject", callback_data=f"reject_{uid}")]
    ])

    text = f"💵 *Withdrawal Request*\n\nUser: `{uid}`\nAmount: ₦{amt}\nBank: {bank}\nAccount: {acct}\nName: {name}"
    updater.bot.send_message(PAYMENT_CHANNEL, text, parse_mode="Markdown", reply_markup=btn)

    update.message.reply_text("✅ Withdrawal submitted! Paid within 20-30 minutes.")

    del context.user_data["step"]
    del context.user_data["wd"]


def admin_panel(update):
    keyboard = [
        [KeyboardButton("/addtask"), KeyboardButton("/addbal")],
        [KeyboardButton("/broadcast"), KeyboardButton("/setreward")],
        [KeyboardButton("/channels")]
    ]
    update.message.reply_text("⚙️ *ADMIN PANEL*", parse_mode="Markdown",
                              reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))


def button(update, context):
    query = update.callback_query
    data = query.data
    uid = update.effective_user.id

    if data == "check_join":
        if check_join(uid):
            query.message.delete()
            context.bot.send_message(uid, "✅ Verified! Welcome.", reply_markup=None)
            main_menu(update)
        else:
            query.answer("❌ You must join all channels.", show_alert=True)

    elif data.startswith("approve_") and uid == ADMIN_ID:
        _, u, amt = data.split("_")
        amt = int(amt)
        cur.execute("UPDATE users SET balance = balance - ? WHERE id=?", (amt, u))
        conn.commit()
        context.bot.send_message(u, f"✅ Your withdrawal of ₦{amt} has been approved!")
        query.message.edit_reply_markup(None)

    elif data.startswith("reject_") and uid == ADMIN_ID:
        _, u = data.split("_")
        context.bot.send_message(u, "❌ Your withdrawal was rejected.")
        query.message.edit_reply_markup(None)


def save_photo(update, context):
    uid = update.effective_user.id
    if "proof_task" not in context.user_data:
        return
    task = context.user_data["proof_task"]

    cur.execute("SELECT 1 FROM proofs WHERE user_id=? AND task_id=?", (uid, task))
    if cur.fetchone():
        update.message.reply_text("❌ Already submitted.")
        return

    cur.execute("INSERT INTO proofs(user_id, task_id) VALUES(?,?)", (uid, task))
    conn.commit()

    cur.execute("SELECT price FROM tasks WHERE id=?", (task,))
    price = cur.fetchone()[0]

    cur.execute("UPDATE users SET balance=balance+? WHERE id=?", (price, uid))
    conn.commit()

    update.message.reply_text(f"✅ Earned ₦{price}!")
    del context.user_data["proof_task"]


updater = Updater(BOT_TOKEN, use_context=True)
dp = updater.dispatcher

dp.add_handler(CommandHandler("start", start))
dp.add_handler(CommandHandler("adminpanel", admin_panel))
dp.add_handler(MessageHandler(Filters.text, text_handler))
dp.add_handler(CallbackQueryHandler(button))
dp.add_handler(MessageHandler(Filters.photo, save_photo))

updater.start_polling()
print("✅ Bot Running...")
