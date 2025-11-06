#!/usr/bin/env python3
# Compact Telegram Task Bot (PTB v13.15) — webhook-ready for Railway
# Keep token & config in env vars (do NOT paste token publicly).

import os, sqlite3, logging
from uuid import uuid4
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Updater, CommandHandler, MessageHandler, Filters,
                          CallbackQueryHandler, CallbackContext)

# --------- Config (from env) ----------
TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
BOT_USERNAME = os.getenv("BOT_USERNAME", "").strip() or "YourBotUsername"
MUST_JOIN = [c.strip() for c in os.getenv("MUST_JOIN", "").split(",") if c.strip()]
PAYMENT_CHANNEL = os.getenv("PAYMENT_CHANNEL", "").strip()
REF_REWARD = int(os.getenv("REF_REWARD", "30") or 30)
MIN_WITHDRAW = int(os.getenv("MIN_WITHDRAW", "500") or 500)
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()  # if set, webhook mode
PORT = int(os.getenv("PORT", "8443"))
DB = os.path.join(os.path.dirname(__file__), "taskbot.db")

# --------- Logging ----------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# --------- DB helpers ----------
def init_db():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT,first TEXT,balance INTEGER DEFAULT 0,referred_by INTEGER,ref_credited INTEGER DEFAULT 0,verified INTEGER DEFAULT 0,joined_at TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,title TEXT,descr TEXT,price INTEGER,link TEXT,active INTEGER DEFAULT 1,created_at TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS subs(id TEXT PRIMARY KEY,task_id TEXT,user_id INTEGER,file_id TEXT,caption TEXT,status TEXT DEFAULT 'pending',created_at TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS withdraws(id TEXT PRIMARY KEY,user_id INTEGER,amount INTEGER,acct TEXT,status TEXT DEFAULT 'pending',created_at TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY,v TEXT)")
    cur.execute("INSERT OR IGNORE INTO settings(k,v) VALUES ('ref',?)", (str(REF_REWARD),))
    cur.execute("INSERT OR IGNORE INTO settings(k,v) VALUES ('minw',?)", (str(MIN_WITHDRAW),))
    con.commit(); con.close()

def db_get_setting(k, default=None):
    con = sqlite3.connect(DB); c = con.cursor()
    c.execute("SELECT v FROM settings WHERE k=?", (k,)); r=c.fetchone(); con.close()
    return r[0] if r else (default if default is not None else "")

def db_set_setting(k,v):
    con = sqlite3.connect(DB); c = con.cursor(); c.execute("INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)",(k,str(v))); con.commit(); con.close()

# --------- user/task helper functions ----------
def add_user(uid, username, first, ref):
    con = sqlite3.connect(DB); c = con.cursor()
    c.execute("INSERT OR IGNORE INTO users(id,username,first,referred_by,joined_at) VALUES(?,?,?,?,datetime('now'))",(uid, username or "", first or "", ref))
    con.commit(); con.close()

def get_user(uid):
    con = sqlite3.connect(DB); c = con.cursor(); c.execute("SELECT id,username,first,balance,referred_by,ref_credited,verified FROM users WHERE id=?", (uid,)); r=c.fetchone(); con.close(); return r

def credit_referrer(ref_id):
    if not ref_id: return
    amt = int(db_get_setting("ref", REF_REWARD))
    con = sqlite3.connect(DB); c = con.cursor(); c.execute("UPDATE users SET balance=balance+? WHERE id=?", (amt, ref_id)); con.commit(); con.close()

def create_task(title, descr, price, link):
    tid = str(uuid4()); con = sqlite3.connect(DB); c = con.cursor()
    c.execute("INSERT INTO tasks(id,title,descr,price,link,created_at) VALUES(?,?,?,?,?,datetime('now'))",(tid,title,descr,price,link))
    con.commit(); con.close(); return tid

def list_tasks():
    con = sqlite3.connect(DB); c = con.cursor(); c.execute("SELECT id,title,price,link FROM tasks WHERE active=1"); r=c.fetchall(); con.close(); return r

def get_task(tid):
    con = sqlite3.connect(DB); c = con.cursor(); c.execute("SELECT id,title,price FROM tasks WHERE id=?", (tid,)); r=c.fetchone(); con.close(); return r

# --------- Handlers ----------
def start(update: Update, ctx: CallbackContext):
    user = update.effective_user
    args = ctx.args
    ref = None
    if args:
        try: ref = int(args[0])
        except: ref = None
    add_user(user.id, user.username or "", user.first_name or "", ref)
    # Notify payments channel about new user and ref (no token leak)
    try:
        ref_text = str(ref) if ref else "None"
        if PAYMENT_CHANNEL:
            ctx.bot.send_message(chat_id=f"@{PAYMENT_CHANNEL}", text=f"New user: {user.mention_html()} (id:{user.id})\nReferred by: {ref_text}", parse_mode="HTML")
    except Exception as e:
        log.warning("notify payment channel failed: %s", e)
    # show must-join
    if MUST_JOIN:
        kb = [[InlineKeyboardButton(f"Join @{c}", url=f"https://t.me/{c}")] for c in MUST_JOIN]
        kb.append([InlineKeyboardButton("I joined ✅", callback_data="i_join")])
        update.message.reply_text("Welcome! Please join required channels and press 'I joined'.", reply_markup=InlineKeyboardMarkup(kb))
    else:
        update.message.reply_text("Welcome! Use /menu")

def check_membership(uid, bot):
    missing=[]
    for ch in MUST_JOIN:
        try:
            mem = bot.get_chat_member(chat_id=f"@{ch}", user_id=uid)
            if mem.status in ("left","kicked"): missing.append(ch)
        except Exception as e:
            log.warning("check membership err %s", e)
            return None, "bot_must_be_admin"
    return missing, None

def i_join_cb(update: Update, ctx: CallbackContext):
    q = update.callback_query; q.answer()
    uid = q.from_user.id
    missing, err = check_membership(uid, ctx.bot)
    if err=="bot_must_be_admin":
        q.message.reply_text("Bot cannot verify joins: make it admin in required channels.")
        return
    if missing:
        q.message.reply_text("You still haven't joined: " + ", ".join(missing))
        return
    # mark verified and credit ref if not credited
    con = sqlite3.connect(DB); c = con.cursor()
    c.execute("SELECT referred_by,ref_credited FROM users WHERE id=?", (uid,)); r=c.fetchone()
    if r and r[0] and not r[1]:
        credit_referrer(r[0]); c.execute("UPDATE users SET ref_credited=1 WHERE id=?", (uid,))
    c.execute("UPDATE users SET verified=1 WHERE id=?", (uid,))
    con.commit(); con.close()
    q.message.reply_text("Verified. You now have access. Use /menu")

def menu(update: Update, ctx: CallbackContext):
    kb = [
        [InlineKeyboardButton("👥 Invite", callback_data="invite")],
        [InlineKeyboardButton("✅ Tasks", callback_data="tasks")],
        [InlineKeyboardButton("💳 Withdraw", callback_data="withdraw")],
        [InlineKeyboardButton("📊 Dashboard", callback_data="dashboard")],
    ]
    update.message.reply_text("Menu:", reply_markup=InlineKeyboardMarkup(kb))

def menu_cb(update: Update, ctx: CallbackContext):
    q = update.callback_query; q.answer(); data=q.data; uid=q.from_user.id
    if data=="invite":
        link = f"https://t.me/{BOT_USERNAME}?start={uid}"
        q.message.reply_text(f"Share this link: {link}\nYou get ₦{db_get_setting('ref', REF_REWARD)} after they join channels.")
    elif data=="tasks":
        rows = list_tasks()
        if not rows: q.message.reply_text("No tasks available.")
        else:
            for tid, title, price, link in rows:
                txt = f"{title}\n₦{price}\n{link or ''}\nID:{tid}"
                kb = [[InlineKeyboardButton("Submit", callback_data=f"submit:{tid}")]]
                q.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb))
    elif data=="withdraw":
        q.message.reply_text("Enter amount to withdraw (integer):"); ctx.user_data["await_withdraw_amt"]=True
    elif data=="dashboard":
        u = get_user(uid)
        if not u: q.message.reply_text("No account found."); return
        q.message.reply_text(f"{u[2]} (@{u[1]})\nBalance: ₦{u[3]}\nReferred by: {u[4] or 'None'}")

def submit_cb(update: Update, ctx: CallbackContext):
    q=update.callback_query; q.answer(); tid=q.data.split(":",1)[1]; ctx.user_data["sub_task"]=tid
    q.message.reply_text("Now send proof: an IMAGE with optional caption (text).")

def photo_handler(update: Update, ctx: CallbackContext):
    user = update.effective_user
    if "sub_task" in ctx.user_data:
        tid = ctx.user_data.pop("sub_task")
        fid = update.message.photo[-1].file_id
        cap = update.message.caption or ""
        sid = str(uuid4())
        con = sqlite3.connect(DB); c = con.cursor()
        c.execute("INSERT INTO subs(id,task_id,user_id,file_id,caption,created_at) VALUES(?,?,?,?,?,datetime('now'))",(sid,tid,user.id,fid,cap))
        con.commit(); con.close()
        update.message.reply_text("Submission received. Wait for admin approval.")
        try: ctx.bot.send_message(chat_id=ADMIN_ID, text=f"New submission {sid} by {user.id} for task {tid}")
        except: pass
    else:
        update.message.reply_text("To submit, click Submit on a task first.")

def text_handler(update: Update, ctx: CallbackContext):
    uid = update.effective_user.id; txt = update.message.text.strip()
    # Withdraw amount flow
    if ctx.user_data.get("await_withdraw_amt"):
        try:
            amt = int(txt); ctx.user_data["withdraw_amt"]=amt; ctx.user_data.pop("await_withdraw_amt",None)
            update.message.reply_text("Send account details (bank/momo):"); ctx.user_data["await_withdraw_acct"]=True; return
        except:
            update.message.reply_text("Send a valid integer amount."); return
    if ctx.user_data.get("await_withdraw_acct"):
        acct = txt; amt = ctx.user_data.pop("withdraw_amt",0); ctx.user_data.pop("await_withdraw_acct",None)
        u = get_user(uid)
        if not u: update.message.reply_text("No account."); return
        minw = int(db_get_setting("minw", MIN_WITHDRAW))
        if amt < minw: update.message.reply_text(f"Minimum withdrawal is ₦{minw}"); return
        if amt > u[3]: update.message.reply_text("Insufficient balance"); return
        wid = str(uuid4()); con = sqlite3.connect(DB); c = con.cursor()
        c.execute("INSERT INTO withdraws(id,user_id,amount,acct,created_at) VALUES(?,?,?,?,datetime('now'))",(wid,uid,amt,acct))
        c.execute("UPDATE users SET balance=balance-? WHERE id=?", (amt,uid))
        con.commit(); con.close()
        update.message.reply_text("Withdrawal placed. Admin will process.")
        try: ctx.bot.send_message(chat_id=f"@{PAYMENT_CHANNEL}", text=f"Withdraw {amt} by {uid}. Acc: {acct}. ID:{wid}")
        except: pass
        return
    # Admin quick commands:
    if update.message.from_user.id == ADMIN_ID:
        if txt.startswith("/addtask"):
            # usage: /addtask Title|price|link(optional)
            parts = txt.split("|")
            if len(parts) < 2: update.message.reply_text("Usage: /addtask Title|price|link(optional)"); return
            title = parts[0].replace("/addtask","").strip() or parts[1].strip()
            price = int(parts[1]) if len(parts)>=2 else 0
            link = parts[2].strip() if len(parts)>=3 else ""
            tid = create_task(title, "", price, link)
            update.message.reply_text(f"Task added: {title} ₦{price} ID:{tid}")
            return
        if txt.startswith("/listsubs"):
            con = sqlite3.connect(DB); c = con.cursor(); c.execute("SELECT id,task_id,user_id,file_id,caption FROM subs WHERE status='pending'"); rows=c.fetchall(); con.close()
            if not rows: update.message.reply_text("No pending subs"); return
            for sid,tid,uid,fid,cap in rows:
                kb = [[InlineKeyboardButton("Approve", callback_data=f"apr:{sid}"), InlineKeyboardButton("Reject", callback_data=f"rej:{sid}")]]
                update.message.reply_photo(photo=fid, caption=f"Sub {sid} by {uid} task {tid}\n{cap}", reply_markup=InlineKeyboardMarkup(kb))
            return
        if txt.startswith("/listwithdraws"):
            con = sqlite3.connect(DB); c = con.cursor(); c.execute("SELECT id,user_id,amount,acct FROM withdraws WHERE status='pending'"); rows=c.fetchall(); con.close()
            if not rows: update.message.reply_text("No pending withdraws"); return
            for wid,uid,amt,acct in rows:
                kb = [[InlineKeyboardButton("Mark Paid", callback_data=f"paid:{wid}"), InlineKeyboardButton("Reject", callback_data=f"wrej:{wid}")]]
                update.message.reply_text(f"Withdraw {wid}\nUser:{uid}\n₦{amt}\n{acct}", reply_markup=InlineKeyboardMarkup(kb))
            return
        if txt.startswith("/setref"):
            try:
                v = int(txt.split(" ",1)[1].strip()); db_set_setting("ref",v); update.message.reply_text(f"Referral set to ₦{v}")
            except: update.message.reply_text("Usage: /setref 30")
            return
        if txt.startswith("/setminw"):
            try:
                v = int(txt.split(" ",1)[1].strip()); db_set_setting("minw",v); update.message.reply_text(f"Min withdraw set ₦{v}")
            except: update.message.reply_text("Usage: /setminw 500")
            return
        if txt.startswith("/broadcast"):
            msg = txt.replace("/broadcast","").strip()
            con = sqlite3.connect(DB); c=con.cursor(); c.execute("SELECT id FROM users"); rows=c.fetchall(); con.close()
            sent=0
            for (uid,) in rows:
                try: ctx.bot.send_message(chat_id=uid, text=msg); sent+=1
                except: pass
            update.message.reply_text(f"Broadcast sent to {sent} users.")
            return

def admin_panel(update: Update, ctx: CallbackContext):
    if update.effective_user.id != ADMIN_ID: update.message.reply_text("Unauthorized"); return
    kb = [
        [InlineKeyboardButton("Add Task (/addtask Title|price|link)", callback_data="adm_add")],
        [InlineKeyboardButton("List Subs", callback_data="adm_listsubs")],
        [InlineKeyboardButton("List Withdraws", callback_data="adm_listw")],
        [InlineKeyboardButton("Set Referral", callback_data="adm_setref"), InlineKeyboardButton("Set Min Withdraw", callback_data="adm_setmin")],
        [InlineKeyboardButton("Broadcast", callback_data="adm_broadcast")]
    ]
    update.message.reply_text("Admin Panel:", reply_markup=InlineKeyboardMarkup(kb))

def admin_cb(update: Update, ctx: CallbackContext):
    q = update.callback_query; q.answer()
    if q.from_user.id != ADMIN_ID: q.message.reply_text("unauth"); return
    data = q.data
    if data=="adm_listsubs": text_handler(update, ctx); return
    if data=="adm_listw": text_handler(update, ctx); return
    if data in ("adm_setref","adm_setmin","adm_broadcast"):
        ctx.user_data["admin_action"]=data; q.message.reply_text("Send value/text now.")
        return
    q.message.reply_text("Use text commands for add/list in this compact version.")

def admin_process_cb(update: Update, ctx: CallbackContext):
    q = update.callback_query; q.answer(); data=q.data
    if q.from_user.id != ADMIN_ID: q.message.reply_text("unauth"); return
    if data.startswith("apr:") or data.startswith("rej:"):
        sid = data.split(":",1)[1]; con = sqlite3.connect(DB); c = con.cursor(); c.execute("SELECT task_id,user_id FROM subs WHERE id=?", (sid,)); r=c.fetchone()
        if not r: q.message.reply_text("not found"); con.close(); return
        task_id, uid = r
        if data.startswith("apr:"):
            c.execute("SELECT price FROM tasks WHERE id=?", (task_id,)); t=c.fetchone(); price = t[0] if t else 0
            c.execute("UPDATE subs SET status='approved' WHERE id=?", (sid,)); c.execute("UPDATE users SET balance=balance+? WHERE id=?", (price, uid)); con.commit(); con.close()
            q.message.reply_text(f"Approved {sid}, paid ₦{price}")
            try: ctx.bot.send_message(chat_id=uid, text=f"Your submission {sid} was approved. You received ₦{price}.")
            except: pass
        else:
            c.execute("UPDATE subs SET status='rejected' WHERE id=?", (sid,)); con.commit(); con.close(); q.message.reply_text("Rejected"); return
    if data.startswith("paid:") or data.startswith("wrej:"):
        wid = data.split(":",1)[1]; con = sqlite3.connect(DB); c = con.cursor(); c.execute("SELECT user_id,amount FROM withdraws WHERE id=?", (wid,)); r=c.fetchone()
        if not r: q.message.reply_text("not found"); con.close(); return
        uid, amt = r
        if data.startswith("paid:"):
            c.execute("UPDATE withdraws SET status='paid' WHERE id=?", (wid,)); con.commit(); con.close(); q.message.reply_text("Marked paid"); 
            try: ctx.bot.send_message(chat_id=uid, text=f"Your withdrawal {wid} was marked paid. ₦{amt}")
            except: pass
        else:
            c.execute("UPDATE withdraws SET status='rejected' WHERE id=?", (wid,)); c.execute("UPDATE users SET balance=balance+? WHERE id=?", (amt, uid)); con.commit(); con.close(); q.message.reply_text("Rejected and refunded"); 
            try: ctx.bot.send_message(chat_id=uid, text=f"Your withdrawal {wid} was rejected. ₦{amt} refunded.")
            except: pass

# --------- Start bot ----------
def main():
    if not TOKEN:
        print("BOT_TOKEN missing in env"); return
    init_db()
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("menu", menu))
    dp.add_handler(CallbackQueryHandler(i_join_cb, pattern="^i_join$"))
    dp.add_handler(CallbackQueryHandler(menu_cb, pattern="^(invite|tasks|withdraw|dashboard)$"))
    dp.add_handler(CallbackQueryHandler(submit_cb, pattern="^submit:"))
    dp.add_handler(MessageHandler(Filters.photo & (~Filters.command), photo_handler))
    dp.add_handler(MessageHandler(Filters.text & (~Filters.command), text_handler))
    dp.add_handler(CommandHandler("adminpanel", admin_panel))
    dp.add_handler(CallbackQueryHandler(admin_cb, pattern="^adm_"))
    dp.add_handler(CallbackQueryHandler(admin_process_cb, pattern="^(apr:|rej:|paid:|wrej:)"))
    # webhook if WEBHOOK_URL provided
    if WEBHOOK_URL:
        url = WEBHOOK_URL.rstrip("/") + "/" + TOKEN
        updater.start_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN)
        updater.bot.set_webhook(url)
        print("Webhook mode set ->", url)
        updater.idle()
    else:
        print("Polling mode")
        updater.start_polling(); updater.idle()

if __name__ == "__main__":
    main()
