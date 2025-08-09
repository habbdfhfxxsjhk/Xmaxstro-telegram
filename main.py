# main.py
# Telegram Store Bot - نسخة أساسية جاهزة بالعديد من الميزات المطلوبة
# يعتمد على python-telegram-bot (v20 async) و sqlite3

import os
import sqlite3
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# === تحميل .env إن وُجد ===
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID") or 0)

if not BOT_TOKEN:
    raise Exception("ضع BOT_TOKEN في المتغيرات البيئية (ENV) قبل التشغيل.")

# === إعداد قاعدة البيانات SQLite ===
DB_PATH = "data.db"
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cur = conn.cursor()

# إنشاء الجداول الأساسية
cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT,
    balance INTEGER DEFAULT 0,
    vip_level TEXT DEFAULT 'None',
    created_at TEXT
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS bans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER UNIQUE,
    reason TEXT,
    banned_at TEXT
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    visible INTEGER DEFAULT 1,
    position INTEGER DEFAULT 0
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    section_id INTEGER,
    name TEXT,
    price INTEGER,
    description TEXT,
    buttons_json TEXT,  -- json string for extra inline buttons
    visible INTEGER DEFAULT 1,
    image_url TEXT,
    position INTEGER DEFAULT 0
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    product_id INTEGER,
    qty INTEGER DEFAULT 1,
    total INTEGER,
    status TEXT DEFAULT 'pending',
    created_at TEXT
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
)
""")

conn.commit()


# === أدوات مساعدة للتعامل مع DB ===

def now_ts():
    return datetime.utcnow().isoformat()

def ensure_user(user_id, username=None):
    cur.execute("SELECT id FROM users WHERE id=?", (user_id,))
    if cur.fetchone() is None:
        cur.execute("INSERT INTO users (id, username, created_at) VALUES (?, ?, ?)",
                    (user_id, username or "", now_ts()))
        conn.commit()

def get_balance(user_id):
    cur.execute("SELECT balance FROM users WHERE id=?", (user_id,))
    r = cur.fetchone()
    return r[0] if r else 0

def set_balance(user_id, amount):
    ensure_user(user_id)
    cur.execute("UPDATE users SET balance = ? WHERE id=?", (amount, user_id))
    conn.commit()

def add_balance(user_id, delta):
    ensure_user(user_id)
    cur.execute("UPDATE users SET balance = balance + ? WHERE id=?", (delta, user_id))
    conn.commit()

def list_users():
    cur.execute("SELECT id, username, balance, vip_level FROM users ORDER BY created_at DESC")
    return cur.fetchall()

def ban_user(user_id, reason=""):
    cur.execute("INSERT OR REPLACE INTO bans (user_id, reason, banned_at) VALUES (?, ?, ?)",
                (user_id, reason, now_ts()))
    conn.commit()

def unban_user(user_id):
    cur.execute("DELETE FROM bans WHERE user_id=?", (user_id,))
    conn.commit()

def is_banned(user_id):
    cur.execute("SELECT 1 FROM bans WHERE user_id=?", (user_id,))
    return cur.fetchone() is not None

def save_setting(key, value):
    cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()

def load_setting(key, default=None):
    cur.execute("SELECT value FROM settings WHERE key=?", (key,))
    r = cur.fetchone()
    return r[0] if r else default

# إعدادات افتراضية
if load_setting("welcome_msg") is None:
    save_setting("welcome_msg", "أهلا بك في متجرنا 🎉\nتصفح الأقسام بالأسفل.")

if load_setting("currency") is None:
    save_setting("currency", "SYP")  # الليرة السورية كمفتاح

# === أدوات المتجر (sections/products) ===

def create_section(name):
    cur.execute("SELECT COALESCE(MAX(position),0)+1 FROM sections")
    pos = cur.fetchone()[0] or 1
    cur.execute("INSERT INTO sections (name, position) VALUES (?, ?)", (name, pos))
    conn.commit()
    return cur.lastrowid

def list_sections(only_visible=True):
    if only_visible:
        cur.execute("SELECT id, name FROM sections WHERE visible=1 ORDER BY position")
    else:
        cur.execute("SELECT id, name, visible FROM sections ORDER BY position")
    return cur.fetchall()

def create_product(section_id, name, price, description="", buttons_json="[]", image_url="", position=None):
    if position is None:
        cur.execute("SELECT COALESCE(MAX(position),0)+1 FROM products WHERE section_id=?", (section_id,))
        position = cur.fetchone()[0] or 1
    cur.execute("""
        INSERT INTO products (section_id, name, price, description, buttons_json, image_url, position)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (section_id, name, price, description, buttons_json, image_url, position))
    conn.commit()
    return cur.lastrowid

def list_products(section_id=None, only_visible=True):
    if section_id is None:
        if only_visible:
            cur.execute("SELECT id, section_id, name, price, description, image_url FROM products WHERE visible=1 ORDER BY position")
        else:
            cur.execute("SELECT id, section_id, name, price, description, visible FROM products ORDER BY position")
    else:
        if only_visible:
            cur.execute("SELECT id, name, price, description, image_url FROM products WHERE section_id=? AND visible=1 ORDER BY position", (section_id,))
        else:
            cur.execute("SELECT id, name, price, description, visible FROM products WHERE section_id=? ORDER BY position", (section_id,))
    return cur.fetchall()

def get_product(product_id):
    cur.execute("SELECT id, section_id, name, price, description, buttons_json, image_url FROM products WHERE id=?", (product_id,))
    return cur.fetchone()

# === أوامر وواجهات البوت ===

# توليد لوحة رئيسية للمستخدم
def main_menu_keyboard():
    kb = [
        [InlineKeyboardButton("🛍️ تصفّح الأقسام", callback_data="browse_sections")],
        [InlineKeyboardButton("💰 رصيدي", callback_data="show_balance"),
         InlineKeyboardButton("📄 طلباتي", callback_data="my_orders")],
        [InlineKeyboardButton("🔔 إشعارات", callback_data="subscriptions")]
    ]
    return InlineKeyboardMarkup(kb)

# لوحة أدمن (شفافة مظهرًا باستخدام emoji وInlineKeyboard)
def admin_panel_keyboard():
    kb = [
        [InlineKeyboardButton("👥 إدارة المستخدمين", callback_data="admin_users")],
        [InlineKeyboardButton("🛒 إدارة المتجر", callback_data="admin_store")],
        [InlineKeyboardButton("✉️ الرسائل والإعلانات", callback_data="admin_messages")],
        [InlineKeyboardButton("⚙️ إعدادات عامة", callback_data="admin_settings")],
    ]
    return InlineKeyboardMarkup(kb)

# الأمر /start
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    username = user.username or user.full_name
    ensure_user(user_id, username)
    if is_banned(user_id):
        await update.message.reply_text("🚫 حسابك محظور. تواصل مع الدعم إذا كان هناك خطأ.")
        return

    welcome = load_setting("welcome_msg", "أهلاً بك!")
    await update.message.reply_text(welcome, reply_markup=main_menu_keyboard())

# الأمر /admin
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("🚫 هذه اللوحة للأدمن فقط.")
        return
    await update.message.reply_text("لوحة الأدمن — تحكم كامل", reply_markup=admin_panel_keyboard())

# Show balance handler
async def show_balance_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user_id = q.from_user.id
    bal = get_balance(user_id)
    currency = load_setting("currency", "SYP")
    await q.edit_message_text(f"💰 رصيدك: {bal} {currency}", reply_markup=main_menu_keyboard())

# Browse sections
async def browse_sections_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    sections = list_sections()
    if not sections:
        await q.edit_message_text("لا توجد أقسام حالياً. تواصل مع الدعم.", reply_markup=main_menu_keyboard())
        return
    kb = []
    for s_id, name in sections:
        kb.append([InlineKeyboardButton(name, callback_data=f"section:{s_id}")])
    kb.append([InlineKeyboardButton("⬅️ رجوع", callback_data="main_back")])
    await q.edit_message_text("📚 الأقسام:", reply_markup=InlineKeyboardMarkup(kb))

# Show section products
async def section_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    payload = q.data  # section:{id}
    _, s_id = payload.split(":")
    s_id = int(s_id)
    products = list_products(s_id)
    if not products:
        await q.edit_message_text("لا توجد منتجات في هذا القسم.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data="browse_sections")]]))
        return
    text = "🛍️ منتجات القسم:\n"
    kb = []
    for p in products:
        pid, name, price, desc, image_url = p[0], p[2], p[3], p[4], p[5]
        text += f"\n• {name} — {price} {load_setting('currency','SYP')}"
        kb.append([InlineKeyboardButton(f"شراء {name}", callback_data=f"buy:{pid}")])
    kb.append([InlineKeyboardButton("⬅️ رجوع", callback_data="browse_sections")])
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

# Buy product flow
async def buy_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, pid = q.data.split(":")
    pid = int(pid)
    prod = get_product(pid)
    if not prod:
        await q.edit_message_text("المنتج غير موجود.", reply_markup=main_menu_keyboard())
        return
    user_id = q.from_user.id
    ensure_user(user_id)
    # هنا نطلب تأكيد الطلب ونسجل طلب مؤقت أو مباشرة نرسله للأدمن
    name = prod[2]
    price = prod[3]
    currency = load_setting("currency", "SYP")
    # تخفيض VIP إن وجد
    cur.execute("SELECT vip_level FROM users WHERE id=?", (user_id,))
    r = cur.fetchone()
    vip = r[0] if r else "None"
    discount = 0
    if vip == "Bronze":
        # مثال: خفض 1%
        discount = int(price * 0.01)
    elif vip == "Silver":
        discount = int(price * 0.02)
    final_price = price - discount
    # سجل الطلب في DB كـ pending
    cur.execute("INSERT INTO orders (user_id, product_id, qty, total, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, pid, 1, final_price, "pending", now_ts()))
    conn.commit()
    order_id = cur.lastrowid
    # أرسل للأدمن إشعار بالطلب
    try:
        admin_msg = f"طلب جديد #{order_id}\nالمنتج: {name}\nالسعر: {final_price} {currency}\nالمستخدم: {q.from_user.id}"
        await context.bot.send_message(chat_id=ADMIN_ID, text=admin_msg,
                                       reply_markup=InlineKeyboardMarkup([
                                           [InlineKeyboardButton("قبول", callback_data=f"admin_order_accept:{order_id}")],
                                           [InlineKeyboardButton("رفض", callback_data=f"admin_order_reject:{order_id}")]
                                       ]))
    except Exception:
        pass
    await q.edit_message_text(f"✅ تم إرسال الطلب #{order_id} إلى الأدمن للمراجعة.\nالسعر: {final_price} {currency}", reply_markup=main_menu_keyboard())

# Admin accepts order
async def admin_order_accept_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != ADMIN_ID:
        await q.edit_message_text("🚫 فقط الأدمن يمكنه تنفيذ هذا.")
        return
    _, action = q.data.split(":")
    # format: admin_order_accept:{order_id}
    parts = q.data.split(":")
    order_id = int(parts[1])
    # استعلام order
    cur.execute("SELECT user_id, product_id, total FROM orders WHERE id=?", (order_id,))
    row = cur.fetchone()
    if not row:
        await q.edit_message_text("الطلب غير موجود.")
        return
    user_id, pid, total = row
    # خصم الرصيد إن اعتمدنا الدفع من رصيد البوت (هنا افتراضي يدوي) -> نقوم فقط بتحديث الحالة
    cur.execute("UPDATE orders SET status='accepted' WHERE id=?", (order_id,))
    conn.commit()
    # إبلاغ المستخدم
    try:
        await context.bot.send_message(chat_id=user_id, text=f"✅ طلبك #{order_id} قُبِل. شكراً لك.")
    except Exception:
        pass
    await q.edit_message_text(f"تم قبول الطلب #{order_id} بنجاح.")

# Admin rejects order
async def admin_order_reject_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != ADMIN_ID:
        await q.edit_message_text("🚫 فقط الأدمن يمكنه تنفيذ هذا.")
        return
    parts = q.data.split(":")
    order_id = int(parts[1])
    cur.execute("UPDATE orders SET status='rejected' WHERE id=?", (order_id,))
    conn.commit()
    cur.execute("SELECT user_id FROM orders WHERE id=?", (order_id,))
    row = cur.fetchone()
    if row:
        user_id = row[0]
        try:
            await context.bot.send_message(chat_id=user_id, text=f"❌ طلبك #{order_id} رُفِض.")
        except Exception:
            pass
    await q.edit_message_text(f"تم رفض الطلب #{order_id}.")

# Admin panel callbacks
async def admin_panel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != ADMIN_ID:
        await q.edit_message_text("🚫 غير مصرح.")
        return
    data = q.data
    if data == "admin_users":
        # عرض المستخدمين (مختصر)
        rows = list_users()
        if not rows:
            await q.edit_message_text("لا يوجد مستخدمين.", reply_markup=admin_panel_keyboard())
            return
        text = "👥 قائمة المستخدمين:\n\n"
        kb = []
        for uid, uname, bal, vip in rows[:30]:  # نعرض أول 30 لتفادي الطوالة
            uname_display = uname or ""
            text += f"• {uname_display} — ID: {uid} — {bal} {load_setting('currency')}\n"
            kb.append([InlineKeyboardButton(f"إدارة {uid}", callback_data=f"admin_user:{uid}")])
        kb.append([InlineKeyboardButton("⬅️ رجوع", callback_data="admin_back")])
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    elif data == "admin_store":
        kb = [
            [InlineKeyboardButton("➕ إضافة قسم", callback_data="admin_add_section")],
            [InlineKeyboardButton("📝 عرض الأقسام", callback_data="admin_list_sections")],
            [InlineKeyboardButton("⬅️ رجوع", callback_data="admin_back")]
        ]
        await q.edit_message_text("🛒 إدارة المتجر:", reply_markup=InlineKeyboardMarkup(kb))
    elif data == "admin_messages":
        kb = [
            [InlineKeyboardButton("✏️ تعديل رسالة الترحيب", callback_data="admin_edit_welcome")],
            [InlineKeyboardButton("📢 بث رسالة", callback_data="admin_broadcast")],
            [InlineKeyboardButton("⬅️ رجوع", callback_data="admin_back")]
        ]
        await q.edit_message_text("✉️ الرسائل:", reply_markup=InlineKeyboardMarkup(kb))
    elif data == "admin_settings":
        kb = [
            [InlineKeyboardButton("🔁 تبديل عملة / إعدادات", callback_data="admin_currency")],
            [InlineKeyboardButton("⬅️ رجوع", callback_data="admin_back")]
        ]
        await q.edit_message_text("⚙️ إعدادات عامة:", reply_markup=InlineKeyboardMarkup(kb))
    elif data == "admin_back":
        await q.edit_message_text("لوحة الأدمن — تحكم كامل", reply_markup=admin_panel_keyboard())
    elif data == "admin_list_sections":
        rows = list_sections(only_visible=False)
        if not rows:
            await q.edit_message_text("لا توجد أقسام.", reply_markup=admin_panel_keyboard())
            return
        kb = []
        text = "الأقسام:\n"
        for sid, name, visible in rows:
            vis = "مرئي" if visible else "مخفي"
            text += f"• [{sid}] {name} — {vis}\n"
            kb.append([InlineKeyboardButton(f"قسم {sid}", callback_data=f"admin_section_manage:{sid}")])
        kb.append([InlineKeyboardButton("⬅️ رجوع", callback_data="admin_store")])
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    elif data.startswith("admin_section_manage:"):
        _, sid = data.split(":")
        sid = int(sid)
        # عرض منتجات القسم وإمكانية تعديل
        cur.execute("SELECT name, visible FROM sections WHERE id=?", (sid,))
        row = cur.fetchone()
        if not row:
            await q.edit_message_text("القسم غير موجود.", reply_markup=admin_panel_keyboard())
            return
        name, visible = row
        kb = [
            [InlineKeyboardButton("➕ إضافة منتج", callback_data=f"admin_add_product:{sid}")],
            [InlineKeyboardButton("📝 قائمة المنتجات", callback_data=f"admin_list_products:{sid}")],
            [InlineKeyboardButton("❌ حذف القسم", callback_data=f"admin_delete_section:{sid}")],
            [InlineKeyboardButton("⬅️ رجوع", callback_data="admin_list_sections")]
        ]
        await q.edit_message_text(f"قسم: {name} (ID: {sid})", reply_markup=InlineKeyboardMarkup(kb))
    elif data.startswith("admin_list_products:"):
        _, sid = data.split(":")
        sid = int(sid)
        prods = list_products(sid, only_visible=False)
        text = f"منتجات القسم {sid}:\n"
        kb = []
        if not prods:
            text += "لا توجد منتجات."
        else:
            for p in prods:
                pid = p[0]
                name = p[2]
                price = p[3]
                vis = "مرئي" if p[4] else "مخفي"
                text += f"• [{pid}] {name} — {price} {load_setting('currency')} — {vis}\n"
                kb.append([InlineKeyboardButton(f"منتج {pid}", callback_data=f"admin_product_manage:{pid}")])
        kb.append([InlineKeyboardButton("⬅️ رجوع", callback_data=f"admin_section_manage:{sid}")])
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    elif data.startswith("admin_product_manage:"):
        _, pid = data.split(":")
        pid = int(pid)
        prod = get_product(pid)
        if not prod:
            await q.edit_message_text("المنتج غير موجود.", reply_markup=admin_panel_keyboard())
            return
        name = prod[2]
        price = prod[3]
        kb = [
            [InlineKeyboardButton("تعديل الاسم", callback_data=f"admin_edit_product_name:{pid}")],
            [InlineKeyboardButton("تعديل السعر", callback_data=f"admin_edit_product_price:{pid}")],
            [InlineKeyboardButton("حذف المنتج", callback_data=f"admin_delete_product:{pid}")],
            [InlineKeyboardButton("⬅️ رجوع", callback_data="admin_store")]
        ]
        await q.edit_message_text(f"المنتج [{pid}] {name}\nالسعر: {price}", reply_markup=InlineKeyboardMarkup(kb))
    elif data == "admin_add_section":
        # نضع حالة انتظار رسالة لادخال اسم القسم
        await q.edit_message_text("أرسل اسم القسم الجديد الآن (أو ألغِ).")
        context.user_data["admin_action"] = "add_section"
    elif data.startswith("admin_add_product:"):
        # اطلب من الأدمن تفاصيل المنتج (بصيغة: اسم | سعر | وصف اختياري)
        _, sid = data.split(":")
        context.user_data["admin_action"] = "add_product"
        context.user_data["admin_section"] = int(sid)
        await q.edit_message_text("أرسل تفاصيل المنتج بصيغة:\nالاسم | السعر | الوصف (الصور والازرار لاحقاً).")
    elif data.startswith("admin_delete_section:"):
        _, sid = data.split(":")
        sid = int(sid)
        cur.execute("DELETE FROM sections WHERE id=?", (sid,))
        cur.execute("DELETE FROM products WHERE section_id=?", (sid,))
        conn.commit()
        await q.edit_message_text(f"تم حذف القسم {sid} وكل منتجاته.", reply_markup=admin_panel_keyboard())
    elif data.startswith("admin_delete_product:"):
        _, pid = data.split(":")
        pid = int(pid)
        cur.execute("DELETE FROM products WHERE id=?", (pid,))
        conn.commit()
        await q.edit_message_text(f"تم حذف المنتج {pid}.", reply_markup=admin_panel_keyboard())
    elif data == "admin_edit_welcome":
        await q.edit_message_text("أرسل النص الجديد لرسالة الترحيب الآن.")
        context.user_data["admin_action"] = "edit_welcome"
    elif data == "admin_broadcast":
        await q.edit_message_text("أرسل رسالة البث الآن. (سيتم إرسالها لكل المستخدمين المسجلين)")
        context.user_data["admin_action"] = "broadcast"
    elif data == "admin_currency":
        await q.edit_message_text("أرسل رمز العملة الجديد (مثال SYP).")
        context.user_data["admin_action"] = "set_currency"
    elif data.startswith("admin_user:"):
        _, uid = data.split(":")
        uid = int(uid)
        # إظهار خيارات إدارة مستخدم
        kb = [
            [InlineKeyboardButton("➕ إضافة رصيد", callback_data=f"admin_user_add:{uid}")],
            [InlineKeyboardButton("➖ خصم رصيد", callback_data=f"admin_user_sub:{uid}")],
            [InlineKeyboardButton("🔄 تصفير رصيد", callback_data=f"admin_user_reset:{uid}")],
            [InlineKeyboardButton("🚫 حظر", callback_data=f"admin_user_ban:{uid}")],
            [InlineKeyboardButton("✅ فك الحظر", callback_data=f"admin_user_unban:{uid}")],
            [InlineKeyboardButton("✉️ إرسال رسالة", callback_data=f"admin_user_msg:{uid}")],
            [InlineKeyboardButton("⬅️ رجوع", callback_data="admin_users")]
        ]
        await q.edit_message_text(f"إدارة المستخدم {uid}:", reply_markup=InlineKeyboardMarkup(kb))
    elif data.startswith("admin_user_add:"):
        _, uid = data.split(":")
        context.user_data["admin_action"] = "user_add_balance"
        context.user_data["admin_target"] = int(uid)
        await q.edit_message_text(f"أدخل المبلغ الذي تريد إضافته للمستخدم {uid}:")
    elif data.startswith("admin_user_sub:"):
        _, uid = data.split(":")
        context.user_data["admin_action"] = "user_sub_balance"
        context.user_data["admin_target"] = int(uid)
        await q.edit_message_text(f"أدخل المبلغ الذي تريد خصمه من المستخدم {uid}:")
    elif data.startswith("admin_user_reset:"):
        _, uid = data.split(":")
        uid = int(uid)
        set_balance(uid, 0)
        await q.edit_message_text(f"تم تصفير رصيد المستخدم {uid}.", reply_markup=admin_panel_keyboard())
    elif data.startswith("admin_user_ban:"):
        _, uid = data.split(":")
        uid = int(uid)
        ban_user(uid, reason="banned by admin")
        await q.edit_message_text(f"تم حظر المستخدم {uid}.", reply_markup=admin_panel_keyboard())
    elif data.startswith("admin_user_unban:"):
        _, uid = data.split(":")
        uid = int(uid)
        unban_user(uid)
        await q.edit_message_text(f"تم فك حظر المستخدم {uid}.", reply_markup=admin_panel_keyboard())
    elif data.startswith("admin_user_msg:"):
        _, uid = data.split(":")
        context.user_data["admin_action"] = "send_msg_to_user"
        context.user_data["admin_target"] = int(uid)
        await q.edit_message_text(f"اكتب الرسالة التي تريد إرسالها للمستخدم {uid}:")
    else:
        await q.edit_message_text("زر غير معروف — أعد المحاولة.", reply_markup=admin_panel_keyboard())

# معالجة رسائل الأدمن في حالات الإدخال
async def admin_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    text = (update.message.text or "").strip()
    action = context.user_data.get("admin_action")
    if not action:
        await update.message.reply_text("استخدم لوحة الأدمن أو أعد المحاولة.")
        return
    if action == "add_section":
        name = text
        sid = create_section(name)
        await update.message.reply_text(f"✅ تم إنشاء القسم '{name}' برقم ID: {sid}")
    elif action == "add_product":
        sid = context.user_data.get("admin_section")
        parts = [p.strip() for p in text.split("|")]
        if len(parts) < 2:
            await update.message.reply_text("خطأ. أرسل: الاسم | السعر | الوصف (اختياري).")
        else:
            name = parts[0]
            try:
                price = int(parts[1])
            except ValueError:
                await update.message.reply_text("السعر يجب أن يكون رقماً صحيحاً.")
                context.user_data.pop("admin_action", None)
                return
            desc = parts[2] if len(parts) >= 3 else ""
            pid = create_product(sid, name, price, desc)
            await update.message.reply_text(f"✅ تم إضافة المنتج '{name}' (ID: {pid}).")
    elif action == "edit_welcome":
        save_setting("welcome_msg", text)
        await update.message.reply_text("✅ تم تحديث رسالة الترحيب.")
    elif action == "broadcast":
        # أرسل الرسالة لكل المستخدمين في DB
        cur.execute("SELECT id FROM users")
        rows = cur.fetchall()
        count = 0
        for r in rows:
            uid = r[0]
            try:
                await context.bot.send_message(chat_id=uid, text=text)
                count += 1
            except Exception:
                pass
        await update.message.reply_text(f"تم إرسال البث إلى {count} مستخدم(ـاً).")
    elif action == "set_currency":
        save_setting("currency", text.upper())
        await update.message.reply_text(f"✅ تم ضبط العملة إلى {text.upper()}.")
    elif action == "user_add_balance":
        try:
            amount = int(text)
            target = context.user_data.get("admin_target")
            add_balance(target, amount)
            await update.message.reply_text(f"✅ تم إضافة {amount} إلى المستخدم {target}.")
        except Exception:
            await update.message.reply_text("خطأ: أرسل رقماً صحيحاً.")
    elif action == "user_sub_balance":
        try:
            amount = int(text)
            target = context.user_data.get("admin_target")
            add_balance(target, -amount)
            await update.message.reply_text(f"✅ تم خصم {amount} من المستخدم {target}.")
        except Exception:
            await update.message.reply_text("خطأ: أرسل رقماً صحيحاً.")
    elif action == "send_msg_to_user":
        target = context.user_data.get("admin_target")
        try:
            await context.bot.send_message(chat_id=target, text=text)
            await update.message.reply_text("✅ تم إرسال الرسالة.")
        except Exception:
            await update.message.reply_text("خطأ عند إرسال الرسالة للمستخدم.")
    else:
        await update.message.reply_text("حالة غير معروفة.")
    # بعد التنفيذ نزيل حالة الادمن
    context.user_data.pop("admin_action", None)
    context.user_data.pop("admin_target", None)
    context.user_data.pop("admin_section", None)

# ردود الازرار العامة
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    data = q.data
    # توجيه الأنواع المختلفة
    if data == "show_balance":
        await show_balance_cb(update, context)
    elif data == "browse_sections":
        await browse_sections_cb(update, context)
    elif data.startswith("section:"):
        await section_cb(update, context)
    elif data.startswith("buy:"):
        await buy_cb(update, context)
    elif data.startswith("admin_") or data.startswith("admin"):
        await admin_panel_cb(update, context)
    elif data.startswith("admin_order_accept:"):
        await admin_order_accept_cb(update, context)
    elif data.startswith("admin_order_reject:"):
        await admin_order_reject_cb(update, context)
    else:
        await q.answer("زر غير مفعل بعد.")

# أمر عرض الطلبات للمستخدم
async def my_orders_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    cur.execute("SELECT id, product_id, qty, total, status, created_at FROM orders WHERE user_id=? ORDER BY created_at DESC", (uid,))
    rows = cur.fetchall()
    if not rows:
        await q.edit_message_text("ليس لديك أي طلبات بعد.", reply_markup=main_menu_keyboard())
        return
    text = "🧾 طلباتك:\n"
    for r in rows:
        oid, pid, qty, total, status, created_at = r
        cur.execute("SELECT name FROM products WHERE id=?", (pid,))
        prod = cur.fetchone()
        prod_name = prod[0] if prod else "منتج محذوف"
        text += f"\n#{oid} {prod_name} — {total} {load_setting('currency')} — {status}\n"
    await q.edit_message_text(text, reply_markup=main_menu_keyboard())

# رسالة نصية عامة للمستخدمين (غير الأدمن) — ردود سريعة
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    if is_banned(uid):
        await update.message.reply_text("حسابك محظور.")
        return
    txt = update.message.text or ""
    # بعض الأوامر النصية السهلة
    if txt.strip() == "/balance" or txt.strip().lower() == "رصيدي":
        bal = get_balance(uid)
        await update.message.reply_text(f"💰 رصيدك: {bal} {load_setting('currency')}")
        return
    # رد افتراضي
    await update.message.reply_text("استخدم الأزرار أو /start لتصفح المتجر.", reply_markup=main_menu_keyboard())

# === تهيئة التطبيق وإضافة الhandlers ===

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))

    # Callbacks
    app.add_handler(CallbackQueryHandler(callback_router))

    # Admin text-entry handler (only when admin is typing inputs)
    app.add_handler(MessageHandler(filters.TEXT & filters.User(ADMIN_ID), admin_message_handler))

    # General text messages
    app.add_handler(MessageHandler(filters.TEXT & (~filters.User(ADMIN_ID)), text_handler))

    print("Bot starting...")
    app.run_polling(stop_signals=None)


if __name__ == "__main__":
    main()
