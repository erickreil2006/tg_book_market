# book_market_bot.py
import os
import logging
from datetime import datetime
from pathlib import Path

import telebot
from telebot import types

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# ────────────────── КОНФИГ ──────────────────
# 1) ОБЯЗАТЕЛЬНО: вставь токен из BotFather
TOKEN = os.getenv("BOT_TOKEN") or "8448286448:AAGoXfIag7Zs4rGTy0IawP4MReO4UpzkgKg"

# 2) Для предложки:
#    - MODERATION_CHAT_ID: приватная группа/чат модераторов (бот должен быть участником)
#    - PUBLIC_CHANNEL_ID: канал, куда бот постит одобренные объявления (бот должен быть АДМИНИСТРАТОРОМ)
#    Эти значения мы получим на Шаге 7 и сюда ПОДСТАВИМ.
MODERATION_CHAT_ID = int(os.getenv("MODERATION_CHAT_ID", "-1002895105532"))  # пример: -1002223334445
PUBLIC_CHANNEL_ID   = int(os.getenv("PUBLIC_CHANNEL_ID", "-1002901385429"))  # пример: -1005556667778

# Подключение к базе данных
if 'DATABASE_URL' in os.environ:
    import psycopg2
    from urllib.parse import urlparse
    # Парсим URL базы данных
    db_url = urlparse(os.environ['DATABASE_URL'])
    db_config = {
        'dbname': db_url.path[1:],
        'user': db_url.username,
        'password': db_url.password,
        'host': db_url.hostname,
        'port': db_url.port,
    }
    def get_db_connection():
        return psycopg2.connect(**db_config)
else:
    # Для локальной разработки
    import sqlite3
    def get_db_connection():
        return sqlite3.connect('books_market.db')

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

# ────────────────── БАЗА ДАННЫХ ──────────────────
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS listings (
        id SERIAL PRIMARY KEY,
        user_id INTEGER,
        username TEXT,
        title TEXT,
        author TEXT,
        price TEXT,
        condition TEXT,
        description TEXT,
        photo_file_id TEXT,
        status TEXT,
        created_at TEXT
    )
    """)
    conn.commit()
    conn.close()

def save_listing(listing):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO listings (user_id, username, title, author, price, condition, description, photo_file_id, status, created_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    RETURNING id
    """, (
        listing['user_id'],
        listing.get('username'),
        listing['title'],
        listing['author'],
        listing['price'],
        listing['condition'],
        listing['description'],
        listing.get('photo_file_id'),
        listing['status'],
        listing['created_at']
    ))
    listing_id = cur.fetchone()[0]
    conn.commit()
    conn.close()
    return listing_id

def get_latest_listings(limit=5, offset=0):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM listings WHERE status IN ('approved','pending') ORDER BY id DESC LIMIT %s OFFSET %s", (limit, offset))
    rows = cur.fetchall()
    conn.close()
    return rows

def get_listing_by_id(listing_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM listings WHERE id = %s", (listing_id,))
    row = cur.fetchone()
    conn.close()
    return row

def get_user_listings(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM listings WHERE user_id = %s ORDER BY id DESC", (user_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

def set_listing_status(listing_id, status):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE listings SET status = %s WHERE id = %s", (status, listing_id))
    conn.commit()
    conn.close()

# ────────────────── ПАМЯТЬ ДЛЯ ФОРМЫ ──────────────────
pending = {}  # {chat_id: {...}}

# ────────────────── КЛАВИАТУРЫ ──────────────────
def main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("➕ Добавить объявление")
    kb.add("🔎 Просмотреть объявления", "📁 Мои объявления")
    kb.add("❓ Помощь")
    return kb

def confirm_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add("✅ Подтвердить", "✏️ Отменить")
    return kb

# ────────────────── ТЕКСТ ПОМОЩИ ──────────────────
HELP_TEXT = (
    "Это бот для предложки объявлений о покупке/продаже б/у учебников.\n\n"
    "Как это работает:\n"
    "1) Нажми «Добавить объявление» и следуй шагам.\n"
    "2) Объявление уйдёт на модерацию.\n"
    "3) После одобрения модераторами оно опубликуется в канале.\n\n"
    "Советы:\n"
    "• Укажи username в Telegram, чтобы покупатели могли связаться напрямую.\n"
    "• Боту нужно быть админом в канале (для публикации)."
)

# ────────────────── КОМАНДЫ ──────────────────
@bot.message_handler(commands=['start'])
def cmd_start(message):
    bot.send_message(
        message.chat.id,
        "Привет! Это бот-предложка для учебников. Выбери действие в меню.",
        reply_markup=main_keyboard()
    )

@bot.message_handler(commands=['help'])
def cmd_help(message):
    bot.send_message(message.chat.id, HELP_TEXT, reply_markup=main_keyboard())

# ────────────────── ОСНОВНОЙ РОУТЕР ──────────────────
@bot.message_handler(func=lambda m: True, content_types=['text', 'photo'])
def main_router(message):
    text = message.text or ""

    if text == "➕ Добавить объявление":
        pending[message.chat.id] = {
            'step': 'title',
            'user_id': message.from_user.id,
            'username': message.from_user.username
        }
        bot.send_message(
            message.chat.id,
            "Введи название учебника (например: «Алгебра, 1 курс»):",
            reply_markup=types.ReplyKeyboardRemove()
        )
        return

    if text == "🔎 Просмотреть объявления":
        channel_link = "https://t.me/uchebniki_fu"
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("Перейти в канал 📢", url=channel_link))
        bot.send_message(message.chat.id, "Все объявления доступны в нашем канале:", reply_markup=markup)
        return

    if text == "📁 Мои объявления":
        rows = get_user_listings(message.from_user.id)
        if not rows:
            bot.send_message(message.chat.id, "У тебя пока нет объявлений.", reply_markup=main_keyboard())
            return
        for row in rows:
            send_listing_brief(message.chat.id, row)
        bot.send_message(message.chat.id, "Это все твои объявления.", reply_markup=main_keyboard())
        return

    if text == "❓ Помощь":
        bot.send_message(message.chat.id, HELP_TEXT, reply_markup=main_keyboard())
        return

    if message.chat.id in pending:
        handle_add_flow(message)
        return

    bot.send_message(message.chat.id, "Не понял команду. Используй меню снизу.", reply_markup=main_keyboard())

# ────────────────── FLOW ДОБАВЛЕНИЯ ──────────────────
def handle_add_flow(message):
    chat_id = message.chat.id
    data = pending.get(chat_id, {})
    step = data.get('step')

    if step == 'title':
        data['title'] = message.text.strip()
        data['step'] = 'author'
        bot.send_message(chat_id, "Автор/курс/предмет (например: П. Иванов / 2 курс):")
        return

    if step == 'author':
        data['author'] = message.text.strip()
        data['step'] = 'price'
        bot.send_message(chat_id, "Цена (например: 200 руб или договорная):")
        return

    if step == 'price':
        data['price'] = message.text.strip()
        data['step'] = 'condition'
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        kb.add("Новый", "Хорошее", "Б/У — с заметными следами")
        bot.send_message(chat_id, "Состояние:", reply_markup=kb)
        return

    if step == 'condition':
        data['condition'] = message.text.strip()
        data['step'] = 'description'
        bot.send_message(chat_id, "Краткое описание (страницы, заметки, дефекты):")
        return

    if step == 'description':
        data['description'] = message.text.strip()
        data['step'] = 'photo'
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        kb.add("📷 Добавить фото", "⛔ Пропустить")
        bot.send_message(chat_id, "Можно добавить фото — нажми «Добавить фото» или «Пропустить».", reply_markup=kb)
        return

    if step == 'photo':
        if message.text == "⛔ Пропустить":
            data['photo_file_id'] = None
            data['step'] = 'confirm'
            send_confirm(chat_id, data)
            return
        if message.text == "📷 Добавить фото":
            data['step'] = 'waiting_photo'
            bot.send_message(chat_id, "Пришли фото (одно изображение).")
            return
        bot.send_message(chat_id, "Выбери «Добавить фото» или «Пропустить».")
        return

    if step == 'waiting_photo':
        if message.content_type == 'photo':
            file_id = message.photo[-1].file_id
            data['photo_file_id'] = file_id
            data['step'] = 'confirm'
            send_confirm(chat_id, data)
            return
        else:
            bot.send_message(chat_id, "Пожалуйста, пришли фото как изображение (не как файл).")
            return

    if step == 'confirm':
        if message.text == "✅ Подтвердить":
            listing = {
                'user_id': data['user_id'],
                'username': data.get('username'),
                'title': data['title'],
                'author': data['author'],
                'price': data['price'],
                'condition': data['condition'],
                'description': data['description'],
                'photo_file_id': data.get('photo_file_id'),
                'status': 'pending',
                'created_at': datetime.utcnow().isoformat()
            }
            listing_id = save_listing(listing)
            pending.pop(chat_id, None)
            bot.send_message(chat_id, f"Заявка создана (ID {listing_id}) и отправлена на модерацию. ✅", reply_markup=main_keyboard())
            send_moderation_card(MODERATION_CHAT_ID, listing_id)
            return
        else:
            pending.pop(chat_id, None)
            bot.send_message(chat_id, "Отменено. Можешь начать заново через меню.", reply_markup=main_keyboard())
            return

# ────────────────── ПОМОЩНИКИ ──────────────────
def send_confirm(chat_id, data):
    text = (
        f"Проверь объявление:\n\n"
        f"<b>{data['title']}</b>\n"
        f"Автор/курс: {data['author']}\n"
        f"Цена: {data['price']}\n"
        f"Состояние: {data['condition']}\n\n"
        f"{data['description']}\n\n"
        f"Опубликовать в предложку (на модерацию)?"
    )
    if data.get('photo_file_id'):
        bot.send_photo(chat_id, data['photo_file_id'], caption=text, reply_markup=confirm_keyboard())
    else:
        bot.send_message(chat_id, text, reply_markup=confirm_keyboard())

def send_listing_brief(chat_id, row):
    id_, _, username, title, author, price, condition, description, photo_file_id, status, created_at = row
    text = f"<b>{title}</b>\n{author}\nЦена: {price}\nСостояние: {condition}\nСтатус: {status}\nID: {id_}"
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Подробнее", callback_data=f"view_{id_}"))
    bot.send_message(chat_id, text, reply_markup=kb)

def send_moderation_card(chat_id, listing_id):
    row = get_listing_by_id(listing_id)
    if not row:
        return
    id_, user_id, username, title, author, price, condition, description, photo_file_id, status, created_at = row
    text = (
        f"📝 <b>Новая заявка (ID {id_})</b>\n"
        f"<b>{title}</b>\n{author}\nЦена: {price}\nСостояние: {condition}\n\n{description}\n\n"
        f"Продавец: {('@'+username) if username else user_id}"
    )
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("✅ Одобрить и опубликовать", callback_data=f"approve_{id_}"),
        types.InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{id_}")
    )
    if photo_file_id:
        bot.send_photo(chat_id, photo_file_id, caption=text, reply_markup=kb)
    else:
        bot.send_message(chat_id, text, reply_markup=kb)

def post_to_public_channel(listing_id):
    row = get_listing_by_id(listing_id)
    if not row:
        return
    id_, user_id, username, title, author, price, condition, description, photo_file_id, status, created_at = row
    text = (
        f"📚 <b>{title}</b>\n"
        f"{author}\n"
        f"Цена: {price}\n"
        f"Состояние: {condition}\n\n"
        f"{description}\n\n"
        f"Связь с продавцом: {('@'+username) if username else 'написать в ответ на пост/через бота'}\n"
        f"ID объявления: {id_}"
    )
    if photo_file_id:
        bot.send_photo(PUBLIC_CHANNEL_ID, photo_file_id, caption=text)
    else:
        bot.send_message(PUBLIC_CHANNEL_ID, text)

def is_admin(user_id):
    # Простая проверка - можно добавить конкретные ID админов через переменные окружения
    admin_ids = os.getenv("ADMIN_IDS", "")
    if admin_ids:
        return str(user_id) in admin_ids.split(",")
    # Или проверка через права в чате модерации
    try:
        member = bot.get_chat_member(MODERATION_CHAT_ID, user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False

# ────────────────── CALLBACKS ──────────────────
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    data = call.data
    chat_id = call.message.chat.id

    if data.startswith("view_"):
        listing_id = int(data.split("_", 1)[1])
        row = get_listing_by_id(listing_id)
        if not row:
            bot.answer_callback_query(call.id, "Объявление не найдено.")
            return
        id_, user_id, username, title, author, price, condition, description, photo_file_id, status, created_at = row
        text = (
            f"<b>{title}</b>\n{author}\nЦена: {price}\nСостояние: {condition}\n\n{description}\n\n"
            f"Продавец: {('@'+username) if username else user_id}\n"
            f"Статус: {status}\nID: {id_}"
        )
        if photo_file_id:
            bot.send_photo(chat_id, photo_file_id, caption=text)
        else:
            bot.send_message(chat_id, text)
        return

    if data.startswith("approve_") or data.startswith("reject_"):
        listing_id = int(data.split("_", 1)[1])

        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "Только модераторы могут это делать.")
            return

        if data.startswith("approve_"):
            set_listing_status(listing_id, 'approved')
            bot.answer_callback_query(call.id, "Одобрено ✅")
            post_to_public_channel(listing_id)
        else:
            set_listing_status(listing_id, 'rejected')
            bot.answer_callback_query(call.id, "Отклонено ❌")

        row = get_listing_by_id(listing_id)
        if row:
            id_, user_id, username, title, author, price, condition, description, photo_file_id, status, created_at = row
            bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
            bot.send_message(chat_id, f"Статус объявления ID {id_}: {status}")
        return

# ────────────────── СТАРТ ──────────────────
if __name__ == "__main__":
    init_db()
    print("Бот запущен…")
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
