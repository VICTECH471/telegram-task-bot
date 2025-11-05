# Updated bot.py — generic join buttons + join-check + cooldown + adminpanel (inline)
import logging
import sqlite3
import time
from telegram import *
from telegram.ext import *
import datetime

# ---------------- SETTINGS ---------------- #
BOT_TOKEN = "8014945735:AAFtydPfTWK6qQD5z9WKuUEKD-QWvvGEXCU"
ADMIN_ID = 8051564945

BOT_USERNAME = "CashgiveawayV1Bot"

# Real channel usernames (kept here, but not shown to users)
SPONSOR_CHANNEL = "@jeremyupdates"
PROMOTER_CHANNELS = [
    "@SmartEarnOfficial",
    "@seyi_update",
    "@kingtupdate1",
    "@ffx_updates"
]
PAYMENT_CHANNEL = "@payment_channel001"

# Display labels (what users see). Keep same length/order as actual lists.
DISPLAY_SPONSOR = "⭐ Sponsor Channel"
DISPLAY_PROMOTER_PREFIX = "📢 Promo Channel"
DISPLAY_PAYMENT = "💳 Payment Channel"

REFERRAL_REWARD = 30  # ₦30 per invite
JOIN_CHECK_COOLDOWN = 10  # seconds
# ------------------------------------------- #

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

conn = sqlite3.connect("bot.db", check_same_thread=False)
cur = conn.cursor()

# DB schema (users table: id, balance, referrer). Keep as-is for compatibility.
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

# in-memory cooldown store for join checks {user_id: last_check_ts}
join_cooldowns = {}

# ---------- helper DB functions ----------
def user_exists(uid):
    cur.execute("SELECT 1 FROM users WHERE id=?", (uid,))
    return cur.fetchone() is not None

def add_user(uid, ref=None, username=None, first_name=None):
    """Add user; if ref present credit inviter and announce to payment channel."""
    if not user_exists(uid):
        # insert with referrer in the referrer column
        cur.execute("INSERT INTO users(id, referrer) VALUES(?,?)", (uid, ref))
        conn.commit()

        # credit referral reward to inviter
        if ref:
            try:
                cur.execute("UPDATE users SET balance = balance + ? WHERE id=?", (REFERRAL_REWARD, ref))
                conn.commit()
                # notify inviter (best-effort)
                try:
                    updater.bot.send_message(int(ref), f"🎉 You earned ₦{REFERRAL_REWARD} for inviting @{username or first_name or ref}!")
                except Exception:
                    pass
            except Exception as e:
                logger.info("Referral credit failed: %s", e)

        # announce new user to payment channel (use username if available)
        try:
            uname = username if username else (first_name if first_name else str(uid))
            text = (
                f"👤 *New User Joined*\n\n"
                f"Username: @{uname}\n"
                f"Referral: {'✅ Yes' if ref else '❌ No'}"
            )
            updater.bot.send_message(PAYMENT_CHANNEL, text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.info("Could not announce new user: %s", e)

# ---------- join check logic ----------
def build_channel_role_lists():
    """Return parallel lists: roles_display, channel_usernames (strings)."""
    channel_usernames = [SPONSOR_CHANNEL] + PROMOTER_CHANNELS + [PAYMENT_CHANNEL]
    roles_display = [DISPLAY_SPONSOR]
    # promoters labeled Promo 1...N
    for i in range(len(PROMOTER_CHANNELS)):
        roles_display.append(f"{DISPLAY_PROMOTER_PREFIX} {i+1}")
    roles_display.append(DISPLAY_PAYMENT)
    return roles_display, channel_usernames

def check_join_roles_for_user(uid):
    """
    Returns list of role labels that are missing for the user.
    e.g. ["⭐ Sponsor Channel", "📢 Promo Channel 2"]
    If the bot cannot access a channel (private / not member), treat it as missing and include the role.
    """
    roles_display, channel_usernames = build_channel_role_lists()
    missing_roles = []
    for role_label, ch in zip(roles_display, channel_usernames):
        try:
            member = updater.bot.get_chat_member(chat_id=ch, user_id=uid)
            if member.status in ("left", "kicked"):
                missing_roles.append(role_label)
        except Exception as e:
            # If bot cannot access or channel private and bot not inside, count as missing role.
            logger.info("check_join: could not check %s for %s -> %s", ch, uid, e)
            missing_roles.append(role_label)
    return missing_roles

def build_join_keyboard():
    """Return InlineKeyboardMarkup with generic role-labeled buttons linking to real channels + I Have Joined button."""
    roles_display, channel_usernames = build_channel_role_lists()
    buttons = []
    for role_label, ch in zip(roles_display, channel_usernames):
        # create a public t.me link to the username (works for public channels)
        link = f"https://t.me/{ch.lstrip('@')}"
        buttons.append([InlineKeyboardButton(role_label, url=link)])
    # final row: I Have Joined
    buttons.append([InlineKeyboardButton("✅ I Have Joined", callback_data="check_joined")])
    return InlineKeyboardMarkup(buttons)

# ---------- handlers ----------
def start(update: Update, context: CallbackContext):
    user = update.effective_user
    uid = user.id
    args = context.args
    ref = None
    if args:
        try:
            ref = int(args[0])
        except:
            ref = None
    # Save user (attempt to use username)
    add_user(uid, ref=ref, username=user.username, first_name=user.first_name)

    # Show join menu if not joined
    missing = check_join_roles_for_user(uid)
    if missing:
        text = (
            "🚨 *Before continuing, please join our required channels:*\n\n"
            "You will see buttons below that open each channel. Join them, then press *I Have Joined*.\n\n"
            "If the bot isn't added to a channel as admin/member it may not be able to verify — contact admin in that case."
        )
        update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=build_join_keyboard())
        return

    # already joined all — show main menu
    show_main_menu(update, context)

def show_main_menu(update: Update, context: CallbackContext):
    user = update.effective_user
    uid = user.id
    keyboard = [
        [KeyboardButton("🧾 Earn Tasks"), KeyboardButton("💰 Balance")],
        [KeyboardButton("👥 Referrals"), KeyboardButton("💵 Withdraw")]
    ]
    update.message.reply_text("🏠 *Dashboard*", parse_mode=ParseMode.MARKDOWN, reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

def check_joined_callback(update: Update, context: CallbackContext):
    """Callback when user taps 'I Have Joined' — performs cooldown and checks roles"""
    query = update.callback_query
    uid = query.from_user.id
    now = time.time()
    last = join_cooldowns.get(uid, 0)
    if now - last < JOIN_CHECK_COOLDOWN:
        remaining = int(JOIN_CHECK_COOLDOWN - (now - last))
        # use alert popup so chat not spammed
        return query.answer(f"⏳ Please wait {remaining}s before checking again.", show_alert=True)
    # update cooldown
    join_cooldowns[uid] = now

    missing = check_join_roles_for_user(uid)
    if not missing:
        try:
            # remove the join message and greet user
            query.message.delete()
        except Exception:
            pass
        try:
            query.answer("✅ Verified — you joined all required channels!", show_alert=False)
        except:
            pass
        # Show dashboard
        # Build a fake Update message to call show_main_menu uniformly
        try:
            show_main_menu(update, context)
        except Exception:
            pass
    else:
        # Build response listing missing *role labels* (no usernames revealed)
        text = "❌ You have not joined all required channels yet.\n\nMissing:\n" + "\n".join(f"• {r}" for r in missing) + "\n\nOpen the missing channels (buttons above) and join, then press I Have Joined again."
        # Build keyboard showing only missing buttons + I Have Joined
        # Map missing labels back to the corresponding channel url rows
        roles_display, channel_usernames = build_channel_role_lists()
        buttons = []
        for role_label, ch in zip(roles_display, channel_usernames):
            if role_label in missing:
                buttons.append([InlineKeyboardButton(role_label, url=f"https://t.me/{ch.lstrip('@')}")])
        buttons.append([InlineKeyboardButton("✅ I Have Joined", callback_data="check_joined")])
        try:
            query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(buttons))
        except:
            try:
                query.answer("❌ You have not joined all required channels.", show_alert=True)
            except:
                pass

def text_handler(update: Update, context: CallbackContext):
    msg = update.message.text
    uid = update.effective_user.id

    # require join on all menu actions
    missing = check_join_roles_for_user(uid)
    if missing:
        update.message.reply_text("🚨 You must join required channels first.", reply_markup=build_join_keyboard())
        return

    # existing menu functionality
    if msg == "💰 Balance":
        cur.execute("SELECT balance FROM users WHERE id=?", (uid,))
        row = cur.fetchone()
        bal = row[0] if row else 0
        update.message.reply_text(f"💳 Your Balance: *₦{bal}*", parse_mode=ParseMode.MARKDOWN)
    elif msg == "👥 Referrals":
        cur.execute("SELECT COUNT(*) FROM users WHERE referrer=?", (uid,))
        count = cur.fetchone()[0]
        link = f"https://t.me/{BOT_USERNAME}?start={uid}"
        # also show invite count (same as count)
        cur.execute("SELECT balance FROM users WHERE id=?", (uid,))
        row = cur.fetchone()
        bal = row[0] if row else 0
        update.message.reply_text(f"👤 Referrals: {count}\n👥 Invite Count: {count}\n\n🔗 Referral Link:\n{link}\n\n💰 Balance: ₦{bal}", parse_mode=ParseMode.MARKDOWN)
    elif msg == "🧾 Earn Tasks":
        cur.execute("SELECT id, title, price, link FROM tasks")
        tasks = cur.fetchall()
        if not tasks:
            update.message.reply_text("No tasks available now.")
            return
        for t in tasks:
            btn = [[InlineKeyboardButton("✅ Submit Proof", callback_data=f"proof_{t[0]}")]]
            update.message.reply_text(f"📌 *{t[1]}*\n💰 Reward: ₦{t[2]}\n🔗 {t[3]}", parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(btn))
    elif msg == "💵 Withdraw":
        update.message.reply_text("Enter amount to withdraw (min ₦300):")
        context.user_data["wd"] = True
    elif msg == "⚙ Admin Panel":
        # keep legacy; admin uses /adminpanel command
        update.message.reply_text("Use /adminpanel to open the admin dashboard (admin only).")
    elif msg.isdigit() and "wd" in context.user_data:
        amt = int(msg)
        cur.execute("SELECT balance FROM users WHERE id=?", (uid,))
        row = cur.fetchone()
        bal = row[0] if row else 0
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
    else:
        # fallback
        update.message.reply_text("Use the menu. Send /start to show the join page.")

# ---------- withdraw & approve/reject ----------
def withdraw_request(update: Update, context: CallbackContext):
    uid = update.effective_user.id
    amt = context.user_data["amount"]
    bank = context.user_data["bank"]
    acct = context.user_data["acct"]
    name = context.user_data["acct_name"]

    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Approve", callback_data=f"approve_{uid}_{amt}"),
         InlineKeyboardButton("❌ Reject", callback_data=f"reject_{uid}")]
    ])

    text = f"💵 *Withdrawal Request*\n\nUser: `{get_display_for_user(uid)}`\nAmount: ₦{amt}\nBank: {bank}\nAccount: {acct}\nName: {name}"
    try:
        updater.bot.send_message(PAYMENT_CHANNEL, text, parse_mode=ParseMode.MARKDOWN, reply_markup=btn)
    except Exception as e:
        logger.info("Could not send withdrawal to payment channel: %s", e)
        update.message.reply_text("Could not send to payment channel. Contact admin.")
        return

    update.message.reply_text("✅ Withdrawal submitted! Paid within 20-30 minutes.")
    # clean up
    del context.user_data["step"]
    del context.user_data["wd"]

def get_display_for_user(uid):
    """Return best display name for user: @username or numeric id."""
    try:
        user = updater.bot.get_chat(uid)
        if user.username:
            return f"@{user.username}"
        if user.first_name:
            return f"{user.first_name}"
    except Exception:
        pass
    return str(uid)

# ---------- admin panel (inline) ----------
def adminpanel_command(update: Update, context: CallbackContext):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        return update.message.reply_text("❌ You are not authorized.")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Task", callback_data="admin_addtask"),
         InlineKeyboardButton("💰 Add Balance", callback_data="admin_addbal")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
         InlineKeyboardButton("🎁 Set Referral Reward", callback_data="admin_setref")],
        [InlineKeyboardButton("🔧 Manage Channels", callback_data="admin_channels"),
         InlineKeyboardButton("🏧 Withdrawals", callback_data="admin_withdraws")],
        [InlineKeyboardButton("🔙 Close", callback_data="admin_close")]
    ])
    update.message.reply_text("⚙️ *ADMIN PANEL*\nChoose an action:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

def admin_callback_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    uid = query.from_user.id
    if uid != ADMIN_ID:
        return query.answer("Not authorized", show_alert=True)
    data = query.data

    if data == "admin_addtask":
        query.answer()
        query.message.reply_text("To add a task, send this command in chat:\n\n/addtask title|price|link\n\nExample:\n/addtask Like post|50|https://t.me/...")
    elif data == "admin_addbal":
        query.answer()
        query.message.reply_text("To add balance manually:\n\n/addbal <user_id> <amount>\nExample:\n/addbal 123456789 500")
    elif data == "admin_broadcast":
        query.answer()
        query.message.reply_text("To broadcast to all users use:\n\n/broadcast Your message here")
    elif data == "admin_setref":
        query.answer()
        query.message.reply_text("To set referral reward use:\n\n/setref <amount>\nExample:\n/setref 30")
    elif data == "admin_channels":
        query.answer()
        query.message.reply_text("To add/remove must-join channels use:\n\n/addchannel @channel_username\n/rmchannel @channel_username")
    elif data == "admin_withdraws":
        query.answer()
        query.message.reply_text("Withdrawal requests appear in the Payment Channel. Approve/Reject by clicking buttons in that channel.")
    elif data == "admin_close":
        query.answer()
        try:
            query.message.delete()
        except:
            pass

# ---------- button callback router ----------
def generic_callback_router(update: Update, context: CallbackContext):
    query = update.callback_query
    data = query.data
    uid = query.from_user.id

    # check_joined handler
    if data == "check_joined":
        return check_joined_callback(update, context)

    # admin inline panel actions
    if data.startswith("admin_"):
        return admin_callback_handler(update, context)

    # approve/reject withdrawal (admin only)
    if data.startswith("approve_") and uid == ADMIN_ID:
        try:
            _, u, amt = data.split("_")
            amt = float(amt)
            # deduct from user is already reserved or not? We will deduct now
            cur.execute("UPDATE users SET balance = balance - ? WHERE id=?", (amt, int(u)))
            conn.commit()
            try:
                updater.bot.send_message(int(u), f"✅ Your withdrawal of ₦{amt} has been approved and marked as paid.")
            except Exception:
                pass
            try:
                query.message.edit_reply_markup(None)
            except:
                pass
            query.answer("Approved")
        except Exception as e:
            logger.info("Approve failed: %s", e)
            query.answer("Error approving", show_alert=True)
        return

    if data.startswith("reject_") and uid == ADMIN_ID:
        try:
            _, u = data.split("_")
            # Optionally: refund? current flow didn't deduct yet; if you deducted earlier, refund here.
            try:
                updater.bot.send_message(int(u), "❌ Your withdrawal was rejected. Please contact admin.")
            except:
                pass
            try:
                query.message.edit_reply_markup(None)
            except:
                pass
            query.answer("Rejected")
        except Exception as e:
            logger.info("Reject failed: %s", e)
            query.answer("Error rejecting", show_alert=True)
        return

    # proof submission callback: set pending proof_task for user
    if data.startswith("proof_"):
        try:
            task_id = int(data.split("_", 1)[1])
            context.user_data["proof_task"] = task_id
            query.message.reply_text("📸 Send screenshot of task completed now.")
            query.answer()
        except Exception as e:
            logger.info("proof callback error: %s", e)
            query.answer("Error", show_alert=True)

# ---------- photo handler (proof upload) ----------
def save_photo(update: Update, context: CallbackContext):
    uid = update.effective_user.id
    if "proof_task" not in context.user_data:
        update.message.reply_text("First click Submit Proof on a task.")
        return
    task = context.user_data["proof_task"]

    # one-time check
    cur.execute("SELECT 1 FROM proofs WHERE user_id=? AND task_id=?", (uid, task))
    if cur.fetchone():
        update.message.reply_text("❌ You already submitted this task.")
        del context.user_data["proof_task"]
        return

    # save proof record
    cur.execute("INSERT INTO proofs(user_id, task_id) VALUES(?,?)", (uid, task))
    conn.commit()

    # credit reward instantly (you may want admin approval instead — change here if needed)
    cur.execute("SELECT price FROM tasks WHERE id=?", (task,))
    row = cur.fetchone()
    price = row[0] if row else 0
    cur.execute("UPDATE users SET balance = balance + ? WHERE id=?", (price, uid))
    conn.commit()

    # forward photo to payment channel for admin visibility
    try:
        caption = f"✅ Task Proof Submitted\nUser: {get_display_for_user(uid)}\nTask ID: {task}\nReward: ₦{price}"
        updater.bot.send_photo(chat_id=PAYMENT_CHANNEL, photo=update.message.photo[-1].file_id, caption=caption)
    except Exception as e:
        logger.info("Failed to forward proof: %s", e)

    update.message.reply_text(f"✅ Proof received. You earned ₦{price}. (Admin will verify in paymen
