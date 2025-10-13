import asyncio
import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import aiohttp
import redis.asyncio as redis
from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, BufferedInputFile
from aiogram import Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from cryptography.fernet import Fernet
from translations import translations
import arabic_reshaper
from bidi.algorithm import get_display
from jdatetime import datetime as jdatetime
import redis.asyncio as redis

REDIS_HOST = os.getenv("REDIS_HOST", "redis-13206.c328.europe-west3-1.gce.redns.redis-cloud.com")
REDIS_PORT = int(os.getenv("REDIS_PORT", 13206))
REDIS_USERNAME = os.getenv("REDIS_USERNAME", "default")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "aBYRaTdeRkECvVMyqVFs6macSGSwCBEV")

redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    username=REDIS_USERNAME,
    password=REDIS_PASSWORD,
    decode_responses=True
)
# Configuration
BOT_TOKEN = "7957011724:AAEw6DmIP7Mtu81O3zWFYaBi04NMLz_ftzc"
CHANNEL_ID = "@FyrenPremium"
MERCHANT_ID = "YOUR_ZARINPAL_MERCHANT_ID"
EXCHANGE_API_URL = "https://api.nobitex.ir/v2/orderbook/TRXIRT"
FRAGMENT_API_URL = "https://fragment.com/api"
NOBITEX_API_KEY = "YOUR_NOBITEX_API_KEY"
SUPPORT_CHAT = "https://t.me/ownerpremiland"
WEBHOOK_URL = "YOUR_WEBHOOK_URL"
ENCRYPTION_KEY = b'k3J5g7pQz8Yk4z5Kx6r7m8n9p0q1r2s3t4u5v6w7x8y='
JWT_SECRET = "your_jwt_secret_key_very_secure"
INITIAL_ADMIN_ID = "8327717833"
BANK_CARD_IMAGE = "bank_card_image.jpg"
BANK_CARD_NUMBER = "YOUR_BANK_CARD_NUMBER"
DB_FILE = "bot_database.db"

# Initialize logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Initialize Redis client

# Initialize SQLite database
def init_sqlite_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            username TEXT,
            first_start_time TEXT,
            last_start_time TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bank_cards (
            user_id TEXT,
            timestamp TEXT,
            phone_number TEXT,
            photo_file_id TEXT,
            status TEXT,
            reject_reason TEXT,
            expiry TEXT,
            PRIMARY KEY (user_id, timestamp)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS receipts (
            user_id TEXT,
            timestamp TEXT,
            purchase_type TEXT,
            price INTEGER,
            plan_category TEXT,
            status TEXT,
            photo_file_id TEXT,
            reject_reason TEXT,
            PRIMARY KEY (user_id, timestamp)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            user_id TEXT,
            timestamp TEXT,
            purchase_type TEXT,
            price INTEGER,
            plan_category TEXT,
            target_id TEXT,
            status TEXT,
            PRIMARY KEY (user_id, timestamp)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS plans (
            plan_type TEXT,
            plan_name TEXT,
            price INTEGER,
            PRIMARY KEY (plan_type, plan_name)
        )
    ''')
    # Populate default plans if table is empty
    cursor.execute('SELECT COUNT(*) FROM plans')
    if cursor.fetchone()[0] == 0:
        default_premium = [
            ("premium", "1month", 500000),
            ("premium", "3month", 1350000),
            ("premium", "6month", 2500000)
        ]
        default_stars = [
            ("stars", "10stars", 100000),
            ("stars", "50stars", 450000),
            ("stars", "100stars", 850000)
        ]
        cursor.executemany('INSERT INTO plans (plan_type, plan_name, price) VALUES (?, ?, ?)', default_premium + default_stars)
    conn.commit()
    conn.close()
    logger.info("SQLite database initialized with plans table")

init_sqlite_db()

# Initialize bot and storage
storage = RedisStorage(redis=redis_client)
logger.info("RedisStorage initialized")
bot = Bot(token=BOT_TOKEN)
logger.info("Bot initialized with token")
dp = Dispatcher(storage=storage)
logger.info("Dispatcher initialized")
router = Router()
dp.include_router(router)
logger.info("Router included in dispatcher")
cipher = Fernet(ENCRYPTION_KEY)
logger.info("Fernet cipher initialized")

# States
class UserStates(StatesGroup):
    SELECT_LANGUAGE = State()
    PURCHASE_TYPE = State()
    PURCHASE_FOR = State()
    ENTER_OTHER_PHONE = State()
    ENTER_PHONE_NUMBER = State()
    PURCHASE_CONFIRM = State()
    CONFIRM_PHOTO = State()
    VERIFY_BANK_CARD = State()
    CONFIRM_BANK_CARD = State()
    VERIFY_RECEIPT = State()
    CONFIRM_RECEIPT = State()

class AdminStates(StatesGroup):
    SET_PRICE = State()
    BROADCAST_MESSAGE = State()
    MANAGE_PLANS = State()
    SET_NEW_PLAN_NAME = State()
    SET_NEW_PLAN_PRICE = State()
    EDIT_BUTTONS = State()
    SET_BUTTON_TEXT = State()
    MANAGE_VERIFICATIONS = State()
    ENTER_REJECT_REASON = State()
    VIEW_USER_DATA = State()
    VIEW_USER_DETAILS = State()

# قیمت‌ها
PREMIUM_PRICES = {}
STARS_PRICES = {}

async def load_prices():
    global PREMIUM_PRICES, STARS_PRICES
    PREMIUM_PRICES = {}
    STARS_PRICES = {}
    logger.info("Loading prices from SQLite")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT plan_type, plan_name, price FROM plans')
    plans = cursor.fetchall()
    conn.close()
    for plan_type, plan_name, price in plans:
        try:
            if plan_type == "premium":
                PREMIUM_PRICES[plan_name] = price
            elif plan_type == "stars":
                STARS_PRICES[plan_name] = price
            logger.debug(f"Loaded {plan_type}:{plan_name} = {price}")
        except ValueError as e:
            logger.error(f"Error parsing price for {plan_type}:{plan_name}: {e}")
    logger.info(f"Prices loaded: Premium={PREMIUM_PRICES}, Stars={STARS_PRICES}")
async def save_plan_to_db(plan_type, plan_name, price):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO plans (plan_type, plan_name, price)
        VALUES (?, ?, ?)
    ''', (plan_type, plan_name, price))
    conn.commit()
    conn.close()
    logger.debug(f"Saved plan {plan_type}:{plan_name} = {price} to SQLite")

# Delete plan from SQLite
async def delete_plan_from_db(plan_type, plan_name):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM plans WHERE plan_type = ? AND plan_name = ?', (plan_type, plan_name))
    conn.commit()
    conn.close()
    logger.debug(f"Deleted plan {plan_type}:{plan_name} from SQLite")
async def check_rate_limit(user_id):
    if user_id == INITIAL_ADMIN_ID:
        logger.debug(f"Rate limit bypassed for admin {user_id}")
        return True
    lock_key = f"lock:{user_id}"
    lock_exists = await redis_client.get(lock_key)
    if lock_exists:
        logger.debug(f"User {user_id} is locked due to rate limit")
        return False
    await redis_client.setex(lock_key, 2, "locked")
    logger.debug(f"Lock set for user {user_id} for 3 seconds")
    return True

async def get_user_language(user_id):
    lang = await redis_client.get(f"user:{user_id}:language")
    if not lang:
        lang = "fa"
        await set_user_language(user_id, lang)
    return lang

async def set_user_language(user_id, language):
    await redis_client.set(f"user:{user_id}:language", language)
    logger.debug(f"Set language for user {user_id} to {language}")

# ذخیره و بازیابی اطلاعات در SQLite
async def save_user_to_db(user_id, username, first_start_time=None, last_start_time=None):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    current_time = datetime.now().isoformat()
    cursor.execute('''
        INSERT OR REPLACE INTO users (user_id, username, first_start_time, last_start_time)
        VALUES (?, ?, ?, ?)
    ''', (user_id, username, first_start_time or current_time, last_start_time or current_time))
    conn.commit()
    conn.close()
    logger.debug(f"Saved user {user_id} to SQLite: username={username}")

async def get_user_from_db(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    if user:
        return {"user_id": user[0], "username": user[1], "first_start_time": user[2], "last_start_time": user[3]}
    return None

async def save_bank_card_to_db(user_id, timestamp, phone_number=None, photo_file_id=None, status="pending", reject_reason=None, expiry=None):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO bank_cards (user_id, timestamp, phone_number, photo_file_id, status, reject_reason, expiry)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, timestamp, phone_number, photo_file_id, status, reject_reason, expiry))
    conn.commit()
    conn.close()
    logger.debug(f"Saved bank card for {user_id} at {timestamp} to SQLite")

async def get_bank_card_from_db(user_id, timestamp):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM bank_cards WHERE user_id = ? AND timestamp = ?', (user_id, timestamp))
    card = cursor.fetchone()
    conn.close()
    if card:
        return {
            "user_id": card[0], "timestamp": card[1], "phone_number": card[2],
            "photo_file_id": card[3], "status": card[4], "reject_reason": card[5], "expiry": card[6]
        }
    return None

async def get_all_bank_cards_for_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM bank_cards WHERE user_id = ?', (user_id,))
    cards = cursor.fetchall()
    conn.close()
    return [{
        "user_id": card[0], "timestamp": card[1], "phone_number": card[2],
        "photo_file_id": card[3], "status": card[4], "reject_reason": card[5], "expiry": card[6]
    } for card in cards]

async def save_receipt_to_db(user_id, timestamp, purchase_type, price, plan_category, status="pending_user", photo_file_id=None, reject_reason=None):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO receipts (user_id, timestamp, purchase_type, price, plan_category, status, photo_file_id, reject_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, timestamp, purchase_type, price, plan_category, status, photo_file_id, reject_reason))
    conn.commit()
    conn.close()
    logger.debug(f"Saved receipt for {user_id} at {timestamp} to SQLite")

async def get_receipt_from_db(user_id, timestamp):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM receipts WHERE user_id = ? AND timestamp = ?', (user_id, timestamp))
    receipt = cursor.fetchone()
    conn.close()
    if receipt:
        return {
            "user_id": receipt[0], "timestamp": receipt[1], "purchase_type": receipt[2],
            "price": receipt[3], "plan_category": receipt[4], "status": receipt[5],
            "photo_file_id": receipt[6], "reject_reason": receipt[7]
        }
    return None

async def get_all_receipts_for_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM receipts WHERE user_id = ?', (user_id,))
    receipts = cursor.fetchall()
    conn.close()
    return [{
        "user_id": receipt[0], "timestamp": receipt[1], "purchase_type": receipt[2],
        "price": receipt[3], "plan_category": receipt[4], "status": receipt[5],
        "photo_file_id": receipt[6], "reject_reason": receipt[7]
    } for receipt in receipts]

async def save_order_to_db(user_id, timestamp, purchase_type, price, plan_category, target_id, status="completed"):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO orders (user_id, timestamp, purchase_type, price, plan_category, target_id, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, timestamp, purchase_type, price, plan_category, target_id, status))
    conn.commit()
    conn.close()
    logger.debug(f"Saved order for {user_id} at {timestamp} to SQLite")

async def get_all_orders_for_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM orders WHERE user_id = ?', (user_id,))
    orders = cursor.fetchall()
    conn.close()
    return [{
        "user_id": order[0], "timestamp": order[1], "purchase_type": order[2],
        "price": order[3], "plan_category": order[4], "target_id": order[5], "status": order[6]
    } for order in orders]

async def add_text_to_image(image_path="welcome_image.jpg", text="", text_position="center", font_path="./Vazirmatn.ttf", font_size=100):
    logger.info(f"Starting add_text_to_image: path={image_path}, text={text}")
    try:
        if not os.path.exists(image_path):
            logger.warning(f"Image file not found at {image_path}")
            return None
        image = Image.open(image_path).convert("RGB")
        draw = ImageDraw.Draw(image)
        width, height = image.size
        reshaped_text = arabic_reshaper.reshape(text)
        bidi_text = get_display(reshaped_text)
        try:
            font = ImageFont.truetype(font_path, font_size) if font_path and os.path.exists(font_path) else ImageFont.load_default()
            text_bbox = draw.textbbox((0, 0), bidi_text, font=font)
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]
            text_position = ((width - text_width) // 2, (height - text_height) // 2) if text_position == "center" else text_position
        except Exception as e:
            font = ImageFont.load_default()
            text_bbox = draw.textbbox((0, 0), bidi_text, font=font)
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]
            text_position = ((width - text_width) // 2, (height - text_height) // 2)
        draw.text(text_position, bidi_text, font=font, fill=(255, 255, 255))
        buffer = BytesIO()
        image.save(buffer, format="JPEG")
        buffer.seek(0)
        logger.debug(f"Text added to image successfully: {image_path}")
        return buffer
    except Exception as e:
        logger.error(f"Error adding text to image {image_path}: {e}")
        return None

@router.message(Command("start"))
async def start_command(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    logger.info(f"Start command triggered by user {user_id}")
    await state.clear()
    if not await check_rate_limit(user_id):
        lang = await get_user_language(user_id)
        await message.reply(translations[lang]["rate_limit"])
        return
    user_name = message.from_user.username or message.from_user.first_name or user_id
    current_time = datetime.now().isoformat()

    # بررسی کاربر در SQLite
    user_info = await get_user_from_db(user_id)
    if user_info:
        # به‌روزرسانی نام و زمان آخرین استارت
        await save_user_to_db(user_id, user_name, user_info["first_start_time"], current_time)
        logger.debug(f"Updated user {user_id} in SQLite: username={user_name}")
    else:
        # ثبت کاربر جدید
        await save_user_to_db(user_id, user_name, current_time, current_time)
        logger.debug(f"Registered new user {user_id} in SQLite")

    # ذخیره در Redis برای کش
    await redis_client.set(f"user:{user_id}:info", json.dumps({
        "user_id": user_id,
        "username": user_name,
        "first_start_time": user_info["first_start_time"] if user_info else current_time,
        "last_start_time": current_time
    }), ex=3600)  # کش برای 1 ساعت

    # مدیریت ارجاع
    args = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else ""
    if args and args != user_id:
        referrer_id = args
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getChat?chat_id={referrer_id}") as resp:
                    result = await resp.json()
                    if not result.get("ok"):
                        lang = await get_user_language(user_id)
                        await message.reply(translations[lang]["invalid_referral_id"])
                        return
                current_points = int(await redis_client.get(f"user:{referrer_id}:points") or 0)
                await redis_client.set(f"user:{referrer_id}:points", current_points + 10)
                await redis_client.sadd(f"user:{referrer_id}:referrals", user_id)
                logger.info(f"Referral added: {user_id} referred by {referrer_id}")
        except aiohttp.ClientError as e:
            logger.error(f"Client error validating referrer {referrer_id}: {e}")
            lang = await get_user_language(user_id)
            await message.reply(translations[lang]["invalid_referral_id"])
            return

    lang = await get_user_language(user_id)
    welcome_message = translations[lang]["welcome"].format(name=user_name)
    image_text = translations[lang].get("welcome_image", "خوش آمدید، {name}!").format(name=user_name)
    image_buffer = await add_text_to_image("welcome_image.jpg", image_text)
    if image_buffer:
        await message.reply_photo(
            photo=BufferedInputFile(image_buffer.getvalue(), filename="welcome_image.jpg"),
            caption=welcome_message,
            reply_markup=await get_main_menu(lang, user_id)
        )
    else:
        await message.reply(welcome_message, reply_markup=await get_main_menu(lang, user_id))

async def get_main_menu(lang="fa", user_id=None):
    if lang not in translations:
        lang = "fa"
    buttons = await redis_client.lrange(f"main_menu:{lang}", 0, -1)
    if not buttons:
        buttons = [
            translations[lang]["buy_premium"],
            translations[lang]["buy_stars"],
            translations[lang]["support"],
            translations[lang]["guide"],
            translations[lang]["change_language"],
        ]
        if user_id == INITIAL_ADMIN_ID:
            buttons.append(translations[lang]["admin_panel"])
        await redis_client.delete(f"main_menu:{lang}")
        for button in buttons:
            await redis_client.rpush(f"main_menu:{lang}", button)
    keyboard = []
    keyboard.append([KeyboardButton(text=buttons[0])])
    keyboard.append([KeyboardButton(text=buttons[1])])
    keyboard.append([KeyboardButton(text=buttons[2]), KeyboardButton(text=buttons[3]), KeyboardButton(text=buttons[4])])
    if user_id == INITIAL_ADMIN_ID and translations[lang]["admin_panel"] in buttons:
        keyboard.append([KeyboardButton(text=translations[lang]["admin_panel"])])
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=keyboard)

async def get_admin_menu(lang="fa"):
    if lang not in translations:
        lang = "fa"
    buttons = await redis_client.lrange(f"admin_menu:{lang}", 0, -1)
    if not buttons:
        buttons = [
            translations[lang]["manage_prices_premium"],
            translations[lang]["manage_prices_stars"],
            translations[lang]["manage_plans_premium"],
            translations[lang]["manage_plans_stars"],
            translations[lang]["view_user_data"],
            translations[lang]["manage_verifications"],
            translations[lang]["view_orders"],
            translations[lang]["broadcast_message"],
            translations[lang]["view_stats"],
            translations[lang]["backup_database"],
            translations[lang]["edit_main_menu"],
            translations[lang]["back"]
        ]
        await redis_client.delete(f"admin_menu:{lang}")
        for button in buttons:
            await redis_client.rpush(f"admin_menu:{lang}", button)
    keyboard = []
    for i in range(0, len(buttons), 2):
        row = buttons[i:i+2]
        keyboard.append([KeyboardButton(text=btn) for btn in row])
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=keyboard)

@router.message(F.text.in_([translations[lang]["support"] for lang in translations]))
async def support_command(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    await state.clear()
    if not await check_rate_limit(user_id):
        lang = await get_user_language(user_id)
        await message.reply(translations[lang]["rate_limit"])
        return
    lang = await get_user_language(user_id)
    await message.reply(translations[lang]["support_message"].format(support=SUPPORT_CHAT))

@router.message(F.text.in_([translations[lang]["view_user_data"] for lang in translations]), F.from_user.id == int(INITIAL_ADMIN_ID))
async def view_user_data(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    await state.clear()
    if not await check_rate_limit(user_id):
        lang = await get_user_language(user_id)
        await message.reply(translations[lang]["rate_limit"])
        return
    lang = await get_user_language(user_id)
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, username FROM users')
    users = cursor.fetchall()
    conn.close()
    
    if not users:
        await message.reply(translations[lang]["no_users"])
        return
    
    keyboard_rows = []
    for user_id, user_name in users[:10]:
        keyboard_rows.append([InlineKeyboardButton(
            text=f"{user_name} ({user_id})",
            callback_data=f"view_user_details_{user_id}"
        )])
    keyboard_rows.append([InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_admin")])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    await message.reply(translations[lang]["select_user_to_view"], reply_markup=keyboard)
    await state.set_state(AdminStates.VIEW_USER_DATA)

@router.callback_query(StateFilter(AdminStates.VIEW_USER_DATA), F.data.startswith("view_user_details_"))
async def view_user_details(callback_query: types.CallbackQuery, state: FSMContext):
    admin_id = str(callback_query.from_user.id)
    target_user_id = callback_query.data.replace("view_user_details_", "")
    lang = await get_user_language(admin_id)
    
    logger.debug(f"Viewing details for user {target_user_id} by admin {admin_id}")
    
    # دریافت اطلاعات کاربر از SQLite
    user_info = await get_user_from_db(target_user_id)
    if not user_info:
        try:
            user_info = await bot.get_chat(target_user_id)
            user_name = user_info.username or user_info.first_name or target_user_id
            first_start = last_start = "نامشخص"
        except Exception as e:
            logger.error(f"Error fetching user info for {target_user_id}: {e}")
            user_name = target_user_id
            first_start = last_start = "نامشخص"
    else:
        user_name = user_info["username"]
        first_start = user_info["first_start_time"]
        last_start = user_info["last_start_time"]
    
    # کش کردن اطلاعات کاربر در Redis
    await redis_client.set(f"user:{target_user_id}:info", json.dumps({
        "user_id": target_user_id,
        "username": user_name,
        "first_start_time": first_start,
        "last_start_time": last_start
    }), ex=3600)
    
    # دریافت کارت‌های بانکی
    user_bank_cards = await get_all_bank_cards_for_user(target_user_id)
    
    # دریافت فیش‌های پرداخت
    user_receipts = await get_all_receipts_for_user(target_user_id)
    
    # دریافت سفارش‌ها
    user_orders = await get_all_orders_for_user(target_user_id)
    
    response = (
        f"اطلاعات کاربر: {user_name} ({target_user_id})\n"
        f"زمان اولین استارت: {first_start}\n"
        f"زمان آخرین استارت: {last_start}\n\n"
    )
    
    response += "📞 کارت‌های بانکی:\n"
    if user_bank_cards:
        for card in user_bank_cards:
            status_text = {
                "pending_user": "در انتظار تأیید کاربر",
                "pending_admin": "در انتظار تأیید ادمین",
                "approved": "تأیید شده",
                "rejected": "رد شده",
                "canceled": "لغو شده"
            }.get(card["status"], "نامشخص")
            response += (
                f" - زمان: {card['timestamp']}\n"
                f"   شماره تلفن: {card['phone_number']}\n"
                f"   وضعیت: {status_text}\n"
            )
            if card["status"] == "rejected":
                response += f"   دلیل رد: {card['reject_reason']}\n"
            if card["photo_file_id"]:
                response += f"   تصویر کارت: موجود\n"
    else:
        response += "هیچ کارت بانکی ثبت نشده است.\n"
    
    response += "\n📄 فیش‌های پرداخت:\n"
    if user_receipts:
        for receipt in user_receipts:
            status_text = {
                "pending_user": "در انتظار تأیید کاربر",
                "pending_admin": "در انتظار تأیید ادمین",
                "approved": "تأیید شده",
                "rejected": "رد شده",
                "canceled": "لغو شده"
            }.get(receipt["status"], "نامشخص")
            response += (
                f" - زمان: {receipt['timestamp']}\n"
                f"   نوع خرید: {receipt['purchase_type']}\n"
                f"   مبلغ: {receipt['price']:,} IRR\n"
                f"   دسته‌بندی: {receipt['plan_category']}\n"
                f"   وضعیت: {status_text}\n"
            )
            if receipt["status"] == "rejected":
                response += f"   دلیل رد: {receipt['reject_reason']}\n"
            if receipt["photo_file_id"]:
                response += f"   تصویر فیش: موجود\n"
    else:
        response += "هیچ فیش پرداختی ثبت نشده است.\n"
    
    response += "\n📦 سفارش‌ها:\n"
    if user_orders:
        for order in user_orders:
            response += (
                f" - زمان: {order['timestamp']}\n"
                f"   نوع خرید: {order['purchase_type']}\n"
                f"   مبلغ: {order['price']:,} IRR\n"
                f"   دسته‌بندی: {order['plan_category']}\n"
                f"   گیرنده: {order['target_id']}\n"
                f"   وضعیت: {order['status']}\n"
            )
    else:
        response += "هیچ سفارشی ثبت نشده است.\n"
    
    keyboard_rows = []
    for card in user_bank_cards:
        if card["photo_file_id"]:
            keyboard_rows.append([InlineKeyboardButton(
                text=f"تصویر کارت ({card['timestamp']})",
                callback_data=f"view_bank_card_photo_{target_user_id}:{card['timestamp']}"
            )])
    for receipt in user_receipts:
        if receipt["photo_file_id"]:
            keyboard_rows.append([InlineKeyboardButton(
                text=f"تصویر فیش ({receipt['timestamp']})",
                callback_data=f"view_receipt_{target_user_id}:{receipt['timestamp']}"  # اصلاح callback_data
            )])
    keyboard_rows.append([InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_user_data")])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    await callback_query.message.edit_text(response, reply_markup=keyboard)
    await state.set_state(AdminStates.VIEW_USER_DETAILS)
    await state.update_data(target_user_id=target_user_id)
    logger.info(f"Displayed user details for {target_user_id} to admin {admin_id}")
@router.callback_query(StateFilter(AdminStates.VIEW_USER_DETAILS), F.data.startswith("view_bank_card_photo_"))
async def view_bank_card_photo(callback_query: types.CallbackQuery, state: FSMContext):
    admin_id = str(callback_query.from_user.id)
    target_key = callback_query.data.replace("view_bank_card_photo_", "")
    target_user_id, timestamp = target_key.split(":", 1)
    lang = await get_user_language(admin_id)
    
    bank_data = await get_bank_card_from_db(target_user_id, timestamp)
    if not bank_data or not bank_data["photo_file_id"]:
        await callback_query.message.edit_text(translations[lang]["bank_card_photo_not_found"])
        return
    
    status_text = {
        "pending_user": "در انتظار تأیید کاربر",
        "pending_admin": "در انتظار تأیید ادمین",
        "approved": "تأیید شده",
        "rejected": "رد شده",
        "canceled": "لغو شده"
    }.get(bank_data["status"], "نامشخص")
    reject_reason = bank_data.get("reject_reason", "ندارد")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=translations[lang]["back"], callback_data=f"view_user_details_{target_user_id}")]
    ])
    await callback_query.message.edit_text(
        f"تصویر کارت بانکی کاربر {target_user_id}\nشماره تلفن: {bank_data['phone_number']}\nوضعیت: {status_text}\nدلیل رد: {reject_reason}\nزمان: {timestamp}",
        reply_markup=keyboard
    )
    await bot.send_photo(admin_id, photo=bank_data["photo_file_id"])

@router.callback_query(F.data.startswith("view_receipt_"))
async def view_receipt(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = str(callback_query.from_user.id)
    lang = await get_user_language(user_id)
    target_key = callback_query.data.replace("view_receipt_", "")
    try:
        target_user_id, timestamp = target_key.split(":", 1)
    except ValueError:
        logger.error(f"Invalid callback data format: {target_key}")
        await callback_query.message.edit_text(
            translations[lang]["invalid_action"],
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_admin")]
            ])
        )
        return

    logger.debug(f"Processing view_receipt for user {target_user_id}, timestamp: {timestamp}")
    
    receipt_data = await get_receipt_from_db(target_user_id, timestamp)
    logger.debug(f"Receipt data for {target_user_id}:{timestamp}: {receipt_data}")
    if not receipt_data:
        logger.warning(f"No receipt found for user {target_user_id} at {timestamp}")
        await callback_query.message.edit_text(
            translations[lang]["receipt_photo_not_found"],
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_admin")]
            ])
        )
        return
    
    status_text = {
        "pending_user": "در انتظار تأیید کاربر",
        "pending_admin": "در انتظار تأیید ادمین",
        "approved": "تأیید شده",
        "rejected": "رد شده",
        "canceled": "لغو شده"
    }.get(receipt_data["status"], "نامشخص")
    reject_reason = receipt_data.get("reject_reason", "ندارد")
    
    # فقط برای فیش‌های در وضعیت pending_admin دکمه‌های تأیید/رد نمایش داده شوند
    keyboard_buttons = [[InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_verifications")]]
    if receipt_data["status"] == "pending_admin":
        keyboard_buttons.insert(0, [
            InlineKeyboardButton(text=translations[lang]["approve_receipt"], callback_data=f"approve_receipt_{target_user_id}:{timestamp}"),
            InlineKeyboardButton(text=translations[lang]["reject_receipt"], callback_data=f"reject_receipt_{target_user_id}:{timestamp}")
        ])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    message_text = (
        translations[lang]["review_receipt"].format(user_id=target_user_id) +
        f"\nنوع خرید: {receipt_data['purchase_type']}\nمبلغ: {receipt_data['price']:,} IRR\nدسته‌بندی: {receipt_data['plan_category']}\nوضعیت: {status_text}\nدلیل رد: {reject_reason}\nزمان: {timestamp}"
    )
    if receipt_data["status"] == "pending_user":
        message_text += "\n(این فیش هنوز توسط کاربر تأیید نشده است و قابل تأیید یا رد نیست.)"
    
    await callback_query.message.edit_text(message_text, reply_markup=keyboard)
    if receipt_data["photo_file_id"]:
        try:
            await bot.send_photo(user_id, photo=receipt_data["photo_file_id"])
            logger.debug(f"Sent receipt photo to admin {user_id} for user {target_user_id}, timestamp: {timestamp}")
        except Exception as e:
            logger.error(f"Error sending receipt photo to admin {user_id}: {str(e)}")
            await callback_query.message.reply(translations[lang]["error_sending_photo"])
    logger.info(f"Displayed receipt details for user {target_user_id}, timestamp: {timestamp}")
@router.callback_query(F.data == "back_to_admin")
async def back_to_admin(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = str(callback_query.from_user.id)
    lang = await get_user_language(user_id)
    logger.debug(f"User {user_id} navigating back to admin panel")

    # ایجاد کیبورد اینلاین برای پنل ادمین
    buttons = await redis_client.lrange(f"admin_menu:{lang}", 0, -1)
    if not buttons:
        buttons = [
            translations[lang]["manage_prices_premium"],
            translations[lang]["manage_prices_stars"],
            translations[lang]["manage_plans_premium"],
            translations[lang]["manage_plans_stars"],
            translations[lang]["view_user_data"],
            translations[lang]["manage_verifications"],
            translations[lang]["view_orders"],
            translations[lang]["broadcast_message"],
            translations[lang]["view_stats"],
            translations[lang]["backup_database"],
            translations[lang]["edit_main_menu"],
            translations[lang]["back"]
        ]
        await redis_client.delete(f"admin_menu:{lang}")
        for button in buttons:
            await redis_client.rpush(f"admin_menu:{lang}", button)

    keyboard_rows = []
    for i in range(0, len(buttons), 2):
        row = buttons[i:i+2]
        keyboard_rows.append([InlineKeyboardButton(text=btn, callback_data=f"admin_{btn}") for btn in row])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    try:
        await callback_query.message.edit_text(
            translations[lang]["admin_panel"],
            reply_markup=keyboard
        )
        logger.info(f"Returned to admin panel for user {user_id}")
    except Exception as e:
        logger.error(f"Error editing message to return to admin panel for user {user_id}: {str(e)}")
        await callback_query.message.reply(
            translations[lang]["admin_panel"],
            reply_markup=keyboard
        )
        logger.info(f"Sent new message for admin panel for user {user_id}")
    
    await state.clear()

@router.callback_query(StateFilter(AdminStates.VIEW_USER_DETAILS), F.data == "back_to_user_data")
async def back_to_user_data(callback_query: types.CallbackQuery, state: FSMContext):
    admin_id = str(callback_query.from_user.id)
    lang = await get_user_language(admin_id)
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, username FROM users')
    users = cursor.fetchall()
    conn.close()
    
    if not users:
        await callback_query.message.edit_text(translations[lang]["no_users"])
        return
    
    keyboard_rows = []
    for user_id, user_name in users[:10]:
        keyboard_rows.append([InlineKeyboardButton(
            text=f"{user_name} ({user_id})",
            callback_data=f"view_user_details_{user_id}"
        )])
    keyboard_rows.append([InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_admin")])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    await callback_query.message.edit_text(translations[lang]["select_user_to_view"], reply_markup=keyboard)
    await state.set_state(AdminStates.VIEW_USER_DATA)

@router.message(F.text.in_([translations[lang]["back"] for lang in translations]))
async def back_to_main_from_admin(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    lang = await get_user_language(user_id)
    if user_id == INITIAL_ADMIN_ID:
        await state.clear()
        user_name = message.from_user.first_name or message.from_user.username or user_id
        await message.reply(translations[lang]["welcome"].format(name=user_name), reply_markup=await get_main_menu(lang, user_id))
    else:
        await message.reply(translations[lang]["invalid_action"])

@router.message(F.text.in_([translations[lang]["change_language"] for lang in translations]))
async def change_language(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    await state.clear()
    if not await check_rate_limit(user_id):
        lang = await get_user_language(user_id)
        await message.reply(translations[lang]["rate_limit"])
        return
    lang = await get_user_language(user_id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=lang_name, callback_data=f"lang_{lang}")]
        for lang, lang_name in [("en", "English"), ("fa", "فارسی"), ("ar", "العربية"), ("ru", "Русский")]
    ])
    await message.reply(translations[lang]["select_language"], reply_markup=keyboard)
    await state.set_state(UserStates.SELECT_LANGUAGE)

@router.callback_query(StateFilter(UserStates.SELECT_LANGUAGE), F.data.startswith("lang_"))
async def process_language_selection(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = str(callback_query.from_user.id)
    lang = callback_query.data.split("_")[1]
    await set_user_language(user_id, lang)
    if lang not in translations:
        lang = "fa"
    await callback_query.message.delete()
    await callback_query.message.answer(translations[lang]["language_changed"])
    user_name = callback_query.from_user.first_name or callback_query.from_user.username or user_id
    await callback_query.message.answer(translations[lang]["welcome"].format(name=user_name), reply_markup=await get_main_menu(lang, user_id))
    await state.clear()

@router.message(F.text.in_([translations[lang]["guide"] for lang in translations]))
async def guide_command(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    await state.clear()
    if not await check_rate_limit(user_id):
        lang = await get_user_language(user_id)
        await message.reply(translations[lang]["rate_limit"])
        return
    lang = await get_user_language(user_id)
    await message.reply(translations[lang]["guide_content"])

@router.message(F.text.in_([translations[lang]["verify_bank_card"] for lang in translations]))
async def verify_bank_card(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    await state.clear()
    if not await check_rate_limit(user_id):
        lang = await get_user_language(user_id)
        await message.reply(translations[lang]["rate_limit"])
        return
    lang = await get_user_language(user_id)
    # بررسی کارت تأییدشده در SQLite
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT expiry FROM bank_cards WHERE user_id = ? AND status = "approved"', (user_id,))
    approved_card = cursor.fetchone()
    conn.close()
    if approved_card and approved_card[0]:
        expiry_dt = datetime.fromisoformat(approved_card[0])
        if datetime.now() <= expiry_dt:
            await message.reply(translations[lang]["already_verified"])
            return
    await message.reply(translations[lang]["verification_message"])
    await state.set_state(UserStates.VERIFY_BANK_CARD)
async def clear_pending_bank_cards(user_id, current_timestamp=None):
    lang = await get_user_language(user_id)
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # دریافت کارت‌های در انتظار به جز کارت با current_timestamp
    query = 'SELECT timestamp, status FROM bank_cards WHERE user_id = ? AND status IN ("pending_user", "pending_admin")'
    params = [user_id]
    if current_timestamp:
        query += ' AND timestamp != ?'
        params.append(current_timestamp)
    cursor.execute(query, params)
    pending_cards = cursor.fetchall()
    logger.debug(f"Pending bank cards for user {user_id} in SQLite (excluding {current_timestamp}): {pending_cards}")
    
    # دریافت فیش‌های در انتظار
    cursor.execute('SELECT timestamp, status FROM receipts WHERE user_id = ? AND status IN ("pending_user", "pending_admin")', (user_id,))
    pending_receipts = cursor.fetchall()
    logger.debug(f"Pending receipts for user {user_id} in SQLite: {pending_receipts}")
    
    conn.close()

    if pending_receipts:
        # اطلاع‌رسانی وجود فیش در انتظار
        for (timestamp, status) in pending_receipts:
            logger.debug(f"Notifying user {user_id} about pending receipt at {timestamp}")
            await bot.send_message(user_id, translations[lang]["pending_receipt_exists"].format(timestamp=timestamp))
        return False
    
    if pending_cards:
        # اطلاع‌رسانی به کاربر درباره کارت‌های در انتظار و لغو آن‌ها
        for (timestamp, status) in pending_cards:
            logger.debug(f"Notifying user {user_id} about pending bank card at {timestamp} with status {status}")
            await bot.send_message(user_id, translations[lang]["pending_bank_card_exists"].format(timestamp=timestamp))
            # به‌روزرسانی وضعیت به canceled
            await save_bank_card_to_db(user_id, timestamp, status="canceled", reject_reason="عملیات به دلیل ثبت کارت جدید متوقف شد")
            # به‌روزرسانی در Redis
            await redis_client.hset("pending_bank_card_verifications", f"{user_id}:{timestamp}", json.dumps({
                "timestamp": timestamp,
                "status": "canceled",
                "reject_reason": "عملیات به دلیل ثبت کارت جدید متوقف شد"
            }))
            logger.debug(f"Bank card {user_id}:{timestamp} marked as canceled in SQLite and Redis")
        
        # درخواست تأیید برای ادامه با کارت جدید
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=translations[lang]["confirm"], callback_data=f"proceed_new_card_{timestamp}")],
            [InlineKeyboardButton(text=translations[lang]["cancel"], callback_data="cancel_new_card")]
        ])
        await bot.send_message(user_id, translations[lang]["confirm_new_bank_card"], reply_markup=keyboard)
        return False
    
    return True

@router.message(StateFilter(UserStates.ENTER_PHONE_NUMBER), F.contact)
async def handle_contact_share(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    lang = await get_user_language(user_id)
    logger.info(f"Handling contact share for user {user_id}, phone: {message.contact.phone_number}")

    # بررسی خرید در انتظار
    pending_purchase = await redis_client.get(f"user:{user_id}:pending_purchase")
    logger.debug(f"Pending purchase for user {user_id}: {pending_purchase}")
    if not pending_purchase:
        logger.error(f"No pending purchase found for user {user_id}")
        await message.reply(
            translations[lang]["no_pending_purchase"],
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=translations[lang]["buy_premium"], callback_data="retry_premium")],
                [InlineKeyboardButton(text=translations[lang]["buy_stars"], callback_data="retry_stars")]
            ])
        )
        await state.clear()
        return

    phone_number = message.contact.phone_number
    logger.debug(f"Processing phone number {phone_number} for user {user_id}")

    try:
        # به‌روزرسانی pending_purchase با شماره تلفن
        purchase_info = json.loads(pending_purchase)
        purchase_info["phone_number"] = phone_number
        await redis_client.set(f"user:{user_id}:pending_purchase", json.dumps(purchase_info), ex=7200)
        logger.debug(f"Updated pending_purchase with phone number for user {user_id}: {purchase_info}")

        # ارسال پیام برای دریافت عکس کارت
        await message.reply(
            translations[lang]["verification_message"],
            reply_markup=await get_main_menu(lang, user_id)
        )
        logger.debug(f"Verification message sent to user {user_id}")
        await state.set_state(UserStates.VERIFY_BANK_CARD)
    except Exception as e:
        logger.error(f"Error processing contact share for user {user_id}: {str(e)}")
        await message.reply(translations[lang]["error_occurred"], reply_markup=await get_main_menu(lang, user_id))
        await state.clear()

@router.message(StateFilter(UserStates.VERIFY_BANK_CARD), F.photo)
async def handle_bank_card_photo(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    lang = await get_user_language(user_id)
    photo_file_id = message.photo[-1].file_id
    timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    logger.info(f"Handling bank card photo for user {user_id}, timestamp: {timestamp}")

    # پاک‌سازی حالت
    await state.clear()
    logger.debug(f"Cleared state for user {user_id} before processing bank card photo")

    # بررسی کارت‌ها و فیش‌های در انتظار (به جز کارت جدید)
    if not await clear_pending_bank_cards(user_id, current_timestamp=timestamp):
        # اگر کارت یا فیش در انتظاری وجود دارد، منتظر تأیید کاربر می‌مانیم
        await state.set_state(UserStates.CONFIRM_BANK_CARD)
        await state.update_data(photo_file_id=photo_file_id, timestamp=timestamp)
        logger.debug(f"Pending cards or receipts found for user {user_id}, awaiting confirmation")
        return

    # بررسی خرید در انتظار
    pending_purchase = await redis_client.get(f"user:{user_id}:pending_purchase")
    logger.debug(f"Pending purchase for user {user_id}: {pending_purchase}")
    if not pending_purchase:
        logger.error(f"No pending purchase found for user {user_id}")
        await message.reply(
            translations[lang]["no_pending_purchase"],
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=translations[lang]["buy_premium"], callback_data="retry_premium")],
                [InlineKeyboardButton(text=translations[lang]["buy_stars"], callback_data="retry_stars")]
            ])
        )
        await state.clear()
        return

    purchase_info = json.loads(pending_purchase)
    phone_number = purchase_info.get("phone_number", None)
    if not phone_number:
        logger.warning(f"No phone number found in pending_purchase for user {user_id}")
        await message.reply(
            translations[lang]["error_occurred"],
            reply_markup=await get_main_menu(lang, user_id)
        )
        await state.clear()
        return

    try:
        # حذف کارت‌های قدیمی‌تر در SQLite
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM bank_cards WHERE user_id = ? AND timestamp != ? AND status IN ("pending_user", "pending_admin")', (user_id, timestamp))
        conn.commit()
        conn.close()
        logger.debug(f"Deleted older pending bank cards for user {user_id}, keeping timestamp: {timestamp}")

        # ذخیره کارت جدید در SQLite
        await save_bank_card_to_db(user_id, timestamp, phone_number=phone_number, photo_file_id=photo_file_id, status="pending_user")
        logger.info(f"Bank card saved to SQLite for user {user_id}, timestamp: {timestamp}")

        # حذف کارت‌های قدیمی‌تر در Redis
        redis_keys = await redis_client.hkeys("pending_bank_card_verifications")
        for key in redis_keys:
            if key.startswith(f"{user_id}:") and key != f"{user_id}:{timestamp}":
                await redis_client.hdel("pending_bank_card_verifications", key)
                logger.debug(f"Deleted Redis key {key} for user {user_id}")

        # ذخیره در Redis
        await redis_client.hset("pending_bank_card_verifications", f"{user_id}:{timestamp}", json.dumps({
            "photo_file_id": photo_file_id,
            "timestamp": timestamp,
            "phone_number": phone_number,
            "status": "pending_user"
        }))
        await redis_client.expire("pending_bank_card_verifications", 7200)
        logger.info(f"Bank card cached in Redis for user {user_id}, timestamp: {timestamp}")

        # ارسال پیام تأیید
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=translations[lang]["confirm_bank_card"], callback_data=f"confirm_bank_card_{timestamp}")],
            [InlineKeyboardButton(text=translations[lang]["cancel_bank_card"], callback_data=f"cancel_bank_card_{timestamp}")]
        ])
        await message.reply_photo(
            photo=photo_file_id,
            caption=translations[lang]["confirm_bank_card_query"],
            reply_markup=keyboard
        )
        await state.set_state(UserStates.CONFIRM_BANK_CARD)
        logger.debug(f"Sent confirmation message for bank card to user {user_id}")
    except Exception as e:
        logger.error(f"Error processing bank card photo for user {user_id}: {str(e)}")
        await message.reply(translations[lang]["error_occurred"], reply_markup=await get_main_menu(lang, user_id))
        await state.clear()
@router.callback_query(StateFilter(UserStates.CONFIRM_BANK_CARD), F.data.startswith("proceed_new_card_"))
async def proceed_new_card(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = str(callback_query.from_user.id)
    lang = await get_user_language(user_id)
    data = await state.get_data()
    photo_file_id = data.get("photo_file_id")
    timestamp = data.get("timestamp")

    # ادامه فرآیند با کارت جدید
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT phone_number FROM bank_cards WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1', (user_id,))
    last_phone = cursor.fetchone()
    conn.close()
    phone_number = last_phone[0] if last_phone else "unknown"

    await save_bank_card_to_db(user_id, timestamp, phone_number=phone_number, photo_file_id=photo_file_id, status="pending_user")
    await redis_client.hset("pending_bank_card_verifications", f"{user_id}:{timestamp}", json.dumps({
        "photo_file_id": photo_file_id,
        "timestamp": timestamp,
        "phone_number": phone_number,
        "status": "pending_user"
    }))
    await redis_client.expire("pending_bank_card_verifications", 3600)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=translations[lang]["confirm_bank_card"], callback_data=f"confirm_bank_card_{timestamp}")],
        [InlineKeyboardButton(text=translations[lang]["cancel_bank_card"], callback_data=f"cancel_bank_card_{timestamp}")]
    ])
    await callback_query.message.edit_text(
        translations[lang]["confirm_bank_card_query"],
        reply_markup=keyboard
    )
    await state.set_state(UserStates.CONFIRM_BANK_CARD)

@router.callback_query(StateFilter(UserStates.CONFIRM_BANK_CARD), F.data == "cancel_new_card")
async def cancel_new_card(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = str(callback_query.from_user.id)
    lang = await get_user_language(user_id)
    await callback_query.message.delete()
    await callback_query.message.answer(translations[lang]["bank_card_canceled"], reply_markup=await get_main_menu(lang, user_id))
    await state.clear()
@router.callback_query(StateFilter(UserStates.CONFIRM_BANK_CARD), F.data.startswith("confirm_bank_card_"))
async def confirm_bank_card(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = str(callback_query.from_user.id)
    timestamp = callback_query.data.replace("confirm_bank_card_", "")
    lang = await get_user_language(user_id)
    logger.info(f"User {user_id} clicked confirm_bank_card for timestamp: {timestamp}")

    bank_data = await get_bank_card_from_db(user_id, timestamp)
    if not bank_data or not bank_data["photo_file_id"]:
        logger.error(f"Bank card not found or no photo for user {user_id} at {timestamp}")
        await callback_query.message.reply(translations[lang]["bank_card_photo_not_found"])
        await state.clear()
        return

    phone_number = bank_data["phone_number"]

    # حذف درخواست‌های قدیمی‌تر در SQLite
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM bank_cards WHERE user_id = ? AND timestamp != ? AND status IN ("pending_user", "pending_admin")', (user_id, timestamp))
    conn.commit()
    conn.close()
    logger.debug(f"Deleted older pending bank cards for user {user_id}, keeping timestamp: {timestamp}")

    # به‌روزرسانی در SQLite به وضعیت در انتظار ادمین
    await save_bank_card_to_db(user_id, timestamp, phone_number=phone_number, photo_file_id=bank_data["photo_file_id"], status="pending_admin")
    logger.info(f"Bank card status updated to 'pending_admin' in SQLite for {user_id}:{timestamp}")

    # حذف درخواست‌های قدیمی‌تر در Redis
    redis_keys = await redis_client.hkeys("pending_bank_card_verifications")
    for key in redis_keys:
        if key.startswith(f"{user_id}:") and key != f"{user_id}:{timestamp}":
            await redis_client.hdel("pending_bank_card_verifications", key)
            logger.debug(f"Deleted Redis key {key} for user {user_id}")

    # به‌روزرسانی در Redis
    await redis_client.hset("pending_bank_card_verifications", f"{user_id}:{timestamp}", json.dumps({
        "photo_file_id": bank_data["photo_file_id"],
        "timestamp": timestamp,
        "phone_number": phone_number,
        "status": "pending_admin"
    }))
    await redis_client.expire("pending_bank_card_verifications", 7200)
    logger.debug(f"Redis updated for bank card {user_id}:{timestamp}")

    # ارسال پیام به ادمین بدون دکمه‌های تأیید/رد
    caption = (
        f"کارت بانکی جدید از کاربر {user_id}\n"
        f"شماره تلفن: {phone_number}\n"
        f"زمان: {timestamp}\n"
        f"لطفاً از منوی مدیریت احراز هویت‌ها اقدام کنید."
    )
    try:
        await bot.send_photo(
            INITIAL_ADMIN_ID,
            photo=bank_data["photo_file_id"],
            caption=caption
        )
        logger.info(f"Sent bank card photo to admin {INITIAL_ADMIN_ID} for user {user_id}, timestamp: {timestamp}")
    except Exception as e:
        logger.error(f"Error sending bank card photo to admin {INITIAL_ADMIN_ID}: {e}")
        await callback_query.message.reply(translations[lang]["error_occurred"])
        await state.clear()
        return

    # ویرایش پیام تأیید یا ارسال پیام جدید
    try:
        if callback_query.message.photo:  # بررسی وجود عکس در پیام
            await callback_query.message.edit_caption(
                caption=translations[lang]["bank_card_sent_for_review"],
                reply_markup=None
            )
        else:
            await callback_query.message.reply(
                text=translations[lang]["bank_card_sent_for_review"],
                reply_markup=None
            )
    except Exception as e:
        logger.error(f"Error editing or replying message for user {user_id}: {e}")
        await callback_query.message.reply(translations[lang]["bank_card_sent_for_review"], reply_markup=None)
    
    await callback_query.message.reply(translations[lang]["bank_card_under_review"], reply_markup=await get_main_menu(lang, user_id))
    await state.clear()
    logger.info(f"Bank card confirmation completed for user {user_id}, timestamp: {timestamp}")



async def cancel_bank_card(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = str(callback_query.from_user.id)
    timestamp = callback_query.data.replace("cancel_bank_card_", "")
    lang = await get_user_language(user_id)
    
    # به‌روزرسانی در SQLite
    await save_bank_card_to_db(user_id, timestamp, status="canceled")
    
    # حذف از Redis
    await redis_client.hdel("pending_bank_card_verifications", f"{user_id}:{timestamp}")
    
    await callback_query.message.edit_caption(
        caption=translations[lang]["bank_card_canceled"],
        reply_markup=None
    )
    await callback_query.message.reply(translations[lang]["bank_card_canceled"], reply_markup=await get_main_menu(lang, user_id))
    await state.clear()

@router.message(StateFilter(UserStates.VERIFY_RECEIPT), F.photo)
async def handle_receipt_photo(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    lang = await get_user_language(user_id)
    photo_file_id = message.photo[-1].file_id
    timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")  # ساده‌سازی timestamp

    # پاک‌سازی حالت برای جلوگیری از تداخل
    await state.clear()
    logger.debug(f"Cleared state for user {user_id} before processing receipt photo")

    # پاک‌سازی فیش‌های در حال انتظار قبلی برای کاربر
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM receipts WHERE user_id = ? AND status IN ("pending_user", "pending_admin")', (user_id,))
    cursor.execute('SELECT COUNT(*) FROM receipts WHERE user_id = ?', (user_id,))
    remaining_receipts = cursor.fetchone()[0]
    conn.commit()
    conn.close()
    logger.debug(f"Cleared pending receipts for user {user_id}. Remaining receipts: {remaining_receipts}")

    # پاک‌سازی Redis برای فیش‌های در حال انتظار
    redis_keys = await redis_client.hkeys("pending_receipt_verifications")
    for key in redis_keys:
        if key.startswith(f"{user_id}:"):
            await redis_client.hdel("pending_receipt_verifications", key)
    logger.debug(f"Cleared Redis pending receipt keys for user {user_id}")

    # بررسی خرید در انتظار
    pending_purchase = await redis_client.get(f"user:{user_id}:pending_purchase")
    if not pending_purchase:
        logger.error(f"No pending purchase found for user {user_id}")
        await message.reply(translations[lang]["error_occurred"])
        await state.clear()
        return
    
    purchase_info = json.loads(pending_purchase)
    purchase_type = purchase_info.get("purchase_type", "نامشخص")
    price = purchase_info.get("price", 0)
    plan_category = purchase_info.get("plan_category", "نامشخص")
    logger.debug(f"Pending purchase for user {user_id}: {purchase_info}")

    # ذخیره در SQLite
    await save_receipt_to_db(user_id, timestamp, purchase_type, price, plan_category, status="pending_user", photo_file_id=photo_file_id)
    logger.debug(f"Receipt saved for user {user_id} at {timestamp}")

    # ذخیره در Redis
    await redis_client.hset("pending_receipt_verifications", f"{user_id}:{timestamp}", json.dumps({
        "photo_file_id": photo_file_id,
        "timestamp": timestamp,
        "user_id": user_id,
        "status": "pending_user",
        "purchase_type": purchase_type,
        "price": price,
        "plan_category": plan_category
    }))
    await redis_client.expire("pending_receipt_verifications", 3600)
    logger.debug(f"Receipt cached in Redis for user {user_id} at {timestamp}")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=translations[lang]["confirm_receipt"], callback_data=f"confirm_receipt_{timestamp}")],
        [InlineKeyboardButton(text=translations[lang]["cancel_receipt"], callback_data=f"cancel_receipt_{timestamp}")]
    ])
    await message.reply_photo(
        photo=photo_file_id,
        caption=translations[lang]["confirm_receipt_query"],
        reply_markup=keyboard
    )
    await state.set_state(UserStates.CONFIRM_RECEIPT)


@router.callback_query(StateFilter(UserStates.CONFIRM_RECEIPT), F.data.startswith("confirm_receipt_"))
async def confirm_receipt(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = str(callback_query.from_user.id)
    timestamp = callback_query.data.replace("confirm_receipt_", "")
    lang = await get_user_language(user_id)

    # بررسی فیش در دیتابیس
    receipt_data = await get_receipt_from_db(user_id, timestamp)
    if not receipt_data or not receipt_data["photo_file_id"]:
        logger.error(f"Receipt not found or no photo for user {user_id} at {timestamp}")
        await callback_query.message.reply(translations[lang]["receipt_photo_not_found"])
        await state.clear()
        return

    # بررسی خرید در انتظار
    pending_purchase = await redis_client.get(f"user:{user_id}:pending_purchase")
    if not pending_purchase:
        logger.error(f"No pending purchase found for user {user_id} in confirm_receipt")
        await callback_query.message.reply(translations[lang]["error_occurred"])
        await state.clear()
        return

    purchase_info = json.loads(pending_purchase)
    purchase_type = purchase_info.get("purchase_type", "نامشخص")
    price = purchase_info.get("price", 0)
    plan_category = purchase_info.get("plan_category", "نامشخص")
    logger.debug(f"Confirming receipt for user {user_id}: {purchase_info}")

    # حذف فیش‌های در حال انتظار قبلی
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM receipts WHERE user_id = ? AND timestamp != ? AND status IN ("pending_user", "pending_admin")', (user_id, timestamp))
    conn.commit()
    conn.close()

    # به‌روزرسانی در SQLite به وضعیت در انتظار ادمین
    await save_receipt_to_db(user_id, timestamp, purchase_type, price, plan_category, status="pending_admin", photo_file_id=receipt_data["photo_file_id"])

    # حذف فیش‌های قدیمی‌تر در Redis
    redis_keys = await redis_client.hkeys("pending_receipt_verifications")
    for key in redis_keys:
        if key.startswith(f"{user_id}:") and key != f"{user_id}:{timestamp}":
            await redis_client.hdel("pending_receipt_verifications", key)

    # به‌روزرسانی در Redis
    await redis_client.hset("pending_receipt_verifications", f"{user_id}:{timestamp}", json.dumps({
        "photo_file_id": receipt_data["photo_file_id"],
        "timestamp": timestamp,
        "user_id": user_id,
        "status": "pending_admin",
        "purchase_type": purchase_type,
        "price": price,
        "plan_category": plan_category
    }))
    await redis_client.expire("pending_receipt_verifications", 3600)

    # ارسال پیام به ادمین بدون دکمه‌های تأیید/رد
    caption = (
        f"فیش پرداخت جدید از کاربر {user_id}\n"
        f"نوع خرید: {purchase_type}\n"
        f"مبلغ: {price:,} IRR\n"
        f"دسته‌بندی: {plan_category}\n"
        f"زمان: {timestamp}\n"
        f"لطفاً از منوی مدیریت احراز هویت‌ها اقدام کنید."
    )
    try:
        await bot.send_photo(
            INITIAL_ADMIN_ID,
            photo=receipt_data["photo_file_id"],
            caption=caption
        )
    except Exception as e:
        logger.error(f"Error sending receipt photo to admin {INITIAL_ADMIN_ID}: {e}")
        await callback_query.message.reply(translations[lang]["error_occurred"])
        await state.clear()
        return

    await callback_query.message.edit_caption(
        caption=translations[lang]["receipt_photo_sent"],
        reply_markup=None
    )
    await callback_query.message.reply(translations[lang]["receipt_under_review"], reply_markup=await get_main_menu(lang, user_id))
    await state.clear()
    


@router.callback_query(StateFilter(UserStates.CONFIRM_RECEIPT), F.data.startswith("cancel_receipt_"))
async def cancel_receipt(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = str(callback_query.from_user.id)
    timestamp = callback_query.data.replace("cancel_receipt_", "")
    lang = await get_user_language(user_id)
    logger.info(f"Cancel receipt triggered for user {user_id}, timestamp: {timestamp}")

    try:
        # بررسی وجود فیش در دیتابیس
        receipt_data = await get_receipt_from_db(user_id, timestamp)
        logger.debug(f"Receipt data for {user_id}:{timestamp}: {receipt_data}")
        if not receipt_data:
            logger.warning(f"No receipt found for user {user_id} at {timestamp}")
            await callback_query.message.reply(translations[lang]["receipt_photo_not_found"])
            await state.clear()
            return

        # به‌روزرسانی در SQLite
        logger.debug(f"Updating receipt status to 'canceled' in SQLite for {user_id}:{timestamp}")
        await save_receipt_to_db(user_id, timestamp, receipt_data["purchase_type"], receipt_data["price"], 
                               receipt_data["plan_category"], status="canceled", 
                               photo_file_id=receipt_data["photo_file_id"])
        logger.info(f"Receipt status updated to 'canceled' in SQLite for {user_id}:{timestamp}")

        # حذف از Redis
        logger.debug(f"Deleting from Redis: pending_receipt_verifications {user_id}:{timestamp}")
        deleted_count = await redis_client.hdel("pending_receipt_verifications", f"{user_id}:{timestamp}")
        logger.debug(f"Deleted {deleted_count} entries from pending_receipt_verifications for {user_id}:{timestamp}")

        logger.debug(f"Deleting pending purchase from Redis for user {user_id}")
        deleted_purchase = await redis_client.delete(f"user:{user_id}:pending_purchase")
        logger.debug(f"Deleted {deleted_purchase} pending purchase entries for user {user_id}")

        # ویرایش کپشن پیام
        logger.debug(f"Editing message caption to 'receipt_canceled' for user {user_id}")
        await callback_query.message.edit_caption(
            caption=translations[lang]["receipt_canceled"],
            reply_markup=None
        )

        # ارسال پیام پاسخ
        logger.debug(f"Sending reply 'receipt_canceled' with main menu for user {user_id}")
        await callback_query.message.reply(
            translations[lang]["receipt_canceled"],
            reply_markup=await get_main_menu(lang, user_id)
        )

        # پاک‌سازی حالت
        logger.debug(f"Clearing state for user {user_id}")
        await state.clear()
        logger.info(f"Receipt cancellation completed for user {user_id}, timestamp: {timestamp}")

    except Exception as e:
        logger.error(f"Error in cancel_receipt for user {user_id}, timestamp {timestamp}: {str(e)}")
        await callback_query.message.reply(translations[lang]["error_occurred"])
        await state.clear()



        
@router.message(Command("admin"), F.from_user.id == int(INITIAL_ADMIN_ID))
async def admin_command(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    logger.info(f"/admin command triggered by admin {user_id}")
    await state.clear()
    logger.debug(f"State cleared for admin {user_id}")
    if not await check_rate_limit(user_id):
        lang = await get_user_language(user_id)
        await message.reply(translations[lang]["rate_limit"])
        logger.warning(f"Rate limit hit for /admin by admin {user_id}")
        return
    lang = await get_user_language(user_id)
    await message.reply(translations[lang]["admin_panel"], reply_markup=await get_admin_menu(lang))
    logger.info(f"Admin panel sent via /admin to admin {user_id}")

@router.message(F.text == translations["fa"]["admin_panel"], F.from_user.id == int(INITIAL_ADMIN_ID))
async def admin_panel_from_menu(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    logger.info(f"Admin panel accessed via menu by admin {user_id}")
    await state.clear()
    logger.debug(f"State cleared for admin {user_id}")
    if not await check_rate_limit(user_id):
        lang = await get_user_language(user_id)
        await message.reply(translations[lang]["rate_limit"])
        logger.warning(f"Rate limit hit for admin panel by admin {user_id}")
        return
    lang = await get_user_language(user_id)
    await message.reply(translations[lang]["admin_panel"], reply_markup=await get_admin_menu(lang))
    logger.info(f"Admin panel sent via menu to admin {user_id}")

@router.message(F.text.in_([translations[lang]["view_orders"] for lang in translations]), F.from_user.id == int(INITIAL_ADMIN_ID))
async def view_orders(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    await state.clear()
    if not await check_rate_limit(user_id):
        lang = await get_user_language(user_id)
        await message.reply(translations[lang]["rate_limit"])
        return
    lang = await get_user_language(user_id)
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM orders')
    orders = cursor.fetchall()
    conn.close()
    
    if not orders:
        await message.reply(translations[lang]["no_orders"])
        return
    
    for order in orders:
        user_id, timestamp, purchase_type, price, plan_category, target_id, status = order
        status_text = {
            "pending": "در انتظار",
            "completed": "کامل شده",
            "canceled": "لغو شده"
        }.get(status, "نامشخص")
        response = (
            f"سفارش کاربر {user_id} در {timestamp}:\n"
            f"نوع: {purchase_type}\n"
            f"مبلغ: {price:,} IRR\n"
            f"دسته‌بندی: {plan_category}\n"
            f"گیرنده: {target_id}\n"
            f"وضعیت: {status_text}\n"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=translations[lang]["complete_order"], callback_data=f"complete_order_{user_id}:{timestamp}")]
        ]) if status == "pending" else None
        await message.reply(response, reply_markup=keyboard)


@router.callback_query(F.data.startswith("complete_order_"))
async def complete_order(callback_query: types.CallbackQuery, state: FSMContext):
    target_key = callback_query.data.replace("complete_order_", "")
    target_user_id, timestamp = target_key.split(":", 1)
    admin_lang = await get_user_language(str(callback_query.from_user.id))
    user_lang = await get_user_language(target_user_id)
    
    # Retrieve order from SQLite
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM orders WHERE user_id = ? AND timestamp = ?', (target_user_id, timestamp))
    order = cursor.fetchone()
    conn.close()
    
    if not order:
        await callback_query.message.edit_text(translations[admin_lang]["order_not_found"])
        return
    
    user_id, timestamp, purchase_type, price, plan_category, target_id, status = order
    if status != "pending":
        await callback_query.message.edit_text(translations[admin_lang]["order_not_pending"])
        return
    
    # Update order status to completed in SQLite
    await save_order_to_db(
        target_user_id, timestamp, purchase_type, price, plan_category, target_id, status="completed"
    )
    
    # Activate the purchase
    if plan_category == "premium":
        durations = {"1month": 30, "3month": 90, "6month": 180}
        duration = durations.get(purchase_type.split("_")[1], 30)
        expiry = datetime.now() + timedelta(days=duration)
        await redis_client.setex(f"user:{target_id}:premium_expiry", int((expiry - datetime.now()).total_seconds()), expiry.isoformat())
    else:
        stars_amounts = {"10stars": 10, "50stars": 50, "100stars": 100}
        stars = stars_amounts.get(purchase_type.split("_")[1], 10)
        current_stars = int(await redis_client.get(f"user:{target_id}:stars") or 0)
        new_stars = current_stars + stars
        await redis_client.set(f"user:{target_id}:stars", new_stars)
    
    # Notify user
    await bot.send_message(target_id, translations[user_lang]["purchase_activated"])
    await callback_query.message.edit_text(translations[admin_lang]["order_completed_admin"].format(user_id=target_user_id))
    logger.info(f"Order completed for user {target_user_id} at {timestamp}")



@router.message(F.text.in_([translations[lang]["manage_prices_premium"] for lang in translations]), F.from_user.id == int(INITIAL_ADMIN_ID))
async def manage_prices_premium(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    await state.clear()
    if not await check_rate_limit(user_id):
        lang = await get_user_language(user_id)
        await message.reply(translations[lang]["rate_limit"])
        return
    lang = await get_user_language(user_id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{plan} - {price:,} IRR", callback_data=f"price_premium_{plan}")]
        for plan, price in PREMIUM_PRICES.items()
    ] + [[InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_admin")]])
    await message.reply(translations[lang]["select_plan_to_update"], reply_markup=keyboard)
    await state.set_state(AdminStates.SET_PRICE)

@router.message(F.text.in_([translations[lang]["manage_prices_stars"] for lang in translations]), F.from_user.id == int(INITIAL_ADMIN_ID))
async def manage_prices_stars(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    await state.clear()
    if not await check_rate_limit(user_id):
        lang = await get_user_language(user_id)
        await message.reply(translations[lang]["rate_limit"])
        return
    lang = await get_user_language(user_id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{plan} - {price:,} IRR", callback_data=f"price_stars_{plan}")]
        for plan, price in STARS_PRICES.items()
    ] + [[InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_admin")]])
    await message.reply(translations[lang]["select_plan_to_update"], reply_markup=keyboard)
    await state.set_state(AdminStates.SET_PRICE)
@router.callback_query(StateFilter(AdminStates.SET_PRICE), F.data.startswith("price_"))
async def process_price_selection(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = str(callback_query.from_user.id)
    plan_type, plan_name = callback_query.data.split("_")[1:3]
    await state.update_data(plan_type=plan_type, plan_name=plan_name)
    lang = await get_user_language(user_id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=translations[lang]["cancel"], callback_data="cancel_admin")]
    ])
    await callback_query.message.edit_text(translations[lang]["enter_new_price"].format(plan=plan_name), reply_markup=keyboard)
    await state.set_state(AdminStates.SET_PRICE)

@router.message(StateFilter(AdminStates.SET_PRICE))
async def process_price_update(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    lang = await get_user_language(user_id)
    data = await state.get_data()
    plan_type = data.get("plan_type")
    plan_name = data.get("plan_name")
    
    try:
        price = int(message.text.strip())
        if plan_type == "premium":
            PREMIUM_PRICES[plan_name] = price
        elif plan_type == "stars":
            STARS_PRICES[plan_name] = price
        await save_plan_to_db(plan_type, plan_name, price)
        await message.reply(translations[lang]["price_updated"])
    except ValueError as e:
        await message.reply(translations[lang]["invalid_price_format"])
        logger.error(f"Invalid price format for {plan_type}:{plan_name}: {e}")
        return
    await state.clear()


@router.message(F.text.in_([translations[lang]["manage_plans_premium"] for lang in translations]), F.from_user.id == int(INITIAL_ADMIN_ID))
async def manage_plans_premium(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    await state.clear()
    if not await check_rate_limit(user_id):
        lang = await get_user_language(user_id)
        await message.reply(translations[lang]["rate_limit"])
        return
    lang = await get_user_language(user_id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{plan} - {price:,} IRR", callback_data=f"plan_premium_{plan}")]
        for plan, price in PREMIUM_PRICES.items()
    ] + [
        [InlineKeyboardButton(text=translations[lang]["add_new_plan"], callback_data="add_new_plan_premium")],
        [InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_admin")]
    ])
    await message.reply(translations[lang]["select_plan_to_manage"], reply_markup=keyboard)
    await state.set_state(AdminStates.MANAGE_PLANS)


@router.message(F.text.in_([translations[lang]["manage_plans_stars"] for lang in translations]), F.from_user.id == int(INITIAL_ADMIN_ID))
async def manage_plans_stars(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    await state.clear()
    if not await check_rate_limit(user_id):
        lang = await get_user_language(user_id)
        await message.reply(translations[lang]["rate_limit"])
        return
    lang = await get_user_language(user_id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{plan} - {price:,} IRR", callback_data=f"plan_stars_{plan}")]
        for plan, price in STARS_PRICES.items()
    ] + [
        [InlineKeyboardButton(text=translations[lang]["add_new_plan"], callback_data="add_new_plan_stars")],
        [InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_admin")]
    ])
    await message.reply(translations[lang]["select_plan_to_manage"], reply_markup=keyboard)
    await state.set_state(AdminStates.MANAGE_PLANS)

@router.callback_query(StateFilter(AdminStates.MANAGE_PLANS), F.data.startswith("plan_"))
async def process_plan_selection(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = str(callback_query.from_user.id)
    lang = await get_user_language(user_id)
    plan_type, plan_name = callback_query.data.split("_")[1:3]
    await state.update_data(plan_type=plan_type, plan_name=plan_name, action="rename")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=translations[lang]["cancel"], callback_data="cancel_admin")]
    ])
    await callback_query.message.edit_text(translations[lang]["enter_new_plan_name"].format(plan=plan_name), reply_markup=keyboard)
    await state.set_state(AdminStates.SET_NEW_PLAN_NAME)

@router.callback_query(StateFilter(AdminStates.MANAGE_PLANS), F.data.startswith("add_new_plan_"))
async def add_new_plan(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = str(callback_query.from_user.id)
    lang = await get_user_language(user_id)
    plan_type = callback_query.data.split("_")[-1]
    await state.update_data(action="add", plan_type=plan_type)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=translations[lang]["cancel"], callback_data="cancel_admin")]
    ])
    await callback_query.message.edit_text(translations[lang]["enter_new_plan_name"].format(plan=""), reply_markup=keyboard)
    await state.set_
@router.message(StateFilter(AdminStates.SET_NEW_PLAN_NAME))
async def process_new_plan_name(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    lang = await get_user_language(user_id)
    data = await state.get_data()
    action = data.get("action")
    plan_type = data.get("plan_type")
    old_plan_name = data.get("plan_name")
    
    if action == "rename":
        new_plan_name = message.text.strip()
        if plan_type == "premium":
            if old_plan_name in PREMIUM_PRICES:
                price = PREMIUM_PRICES.pop(old_plan_name)
                PREMIUM_PRICES[new_plan_name] = price
                await save_plan_to_db(plan_type, new_plan_name, price)
                await delete_plan_from_db(plan_type, old_plan_name)
        elif plan_type == "stars":
            if old_plan_name in STARS_PRICES:
                price = STARS_PRICES.pop(old_plan_name)
                STARS_PRICES[new_plan_name] = price
                await save_plan_to_db(plan_type, new_plan_name, price)
                await delete_plan_from_db(plan_type, old_plan_name)
        await message.reply(translations[lang]["plan_name_updated"].format(new_name=new_plan_name))
        await state.clear()
    elif action == "add":
        plan_name = message.text.strip()
        await state.update_data(plan_name=plan_name)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=translations[lang]["cancel"], callback_data="cancel_admin")]
        ])
        await message.reply(translations[lang]["enter_new_price"].format(plan=plan_name), reply_markup=keyboard)
        await state.set_state(AdminStates.SET_NEW_PLAN_PRICE)

@router.message(StateFilter(AdminStates.SET_NEW_PLAN_PRICE))
async def process_new_plan_price(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    lang = await get_user_language(user_id)
    data = await state.get_data()
    plan_type = data.get("plan_type")
    plan_name = data.get("plan_name")
    
    try:
        price = int(message.text.strip())
        if plan_type == "premium":
            PREMIUM_PRICES[plan_name] = price
        elif plan_type == "stars":
            STARS_PRICES[plan_name] = price
        await save_plan_to_db(plan_type, plan_name, price)
        await message.reply(translations[lang]["plan_added"].format(name=plan_name))
    except ValueError as e:
        await message.reply(translations[lang]["invalid_price_format"])
        logger.error(f"Invalid price format for {plan_type}:{plan_name}: {e}")
        return
    await state.clear()
@router.message(F.text.in_([translations[lang]["broadcast_message"] for lang in translations]), F.from_user.id == int(INITIAL_ADMIN_ID))
async def broadcast_message(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    await state.clear()
    if not await check_rate_limit(user_id):
        lang = await get_user_language(user_id)
        await message.reply(translations[lang]["rate_limit"])
        return
    lang = await get_user_language(user_id)
    await message.reply(translations[lang]["enter_broadcast_message"])
    await state.set_state(AdminStates.BROADCAST_MESSAGE)

@router.message(StateFilter(AdminStates.BROADCAST_MESSAGE))
async def process_broadcast_message(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    lang = await get_user_language(user_id)
    broadcast_text = message.text.strip()
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM users')
    users = cursor.fetchall()
    conn.close()
    
    sent_count = 0
    for (user_id,) in users:
        user_lang = await get_user_language(user_id)
        try:
            await bot.send_message(user_id, broadcast_text)
            sent_count += 1
        except Exception as e:
            logger.error(f"Failed to send broadcast to {user_id}: {e}")
    await message.reply(translations[lang]["broadcast_sent"].format(count=sent_count))
    await state.clear()

@router.message(F.text.in_([translations[lang]["view_stats"] for lang in translations]), F.from_user.id == int(INITIAL_ADMIN_ID))
async def view_stats(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    await state.clear()
    if not await check_rate_limit(user_id):
        lang = await get_user_language(user_id)
        await message.reply(translations[lang]["rate_limit"])
        return
    lang = await get_user_language(user_id)
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM users')
    users_count = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM orders')
    orders_count = cursor.fetchone()[0]
    conn.close()
    
    response = (
        f"{translations[lang]['stats']}\n"
        f"کاربران: {users_count}\n"
        f"سفارش‌ها: {orders_count}"
    )
    await message.reply(response)

@router.message(F.text.in_([translations[lang]["backup_database"] for lang in translations]), F.from_user.id == int(INITIAL_ADMIN_ID))
async def backup_database(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    await state.clear()
    if not await check_rate_limit(user_id):
        lang = await get_user_language(user_id)
        await message.reply(translations[lang]["rate_limit"])
        return
    lang = await get_user_language(user_id)
    
    backup_file = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sql"
    conn = sqlite3.connect(DB_FILE)
    with open(backup_file, "w") as f:
        for line in conn.iterdump():
            f.write(f"{line}\n")
    conn.close()
    await message.reply(translations[lang]["backup_created"].format(file=backup_file))

@router.message(F.text.in_([translations[lang]["edit_main_menu"] for lang in translations]), F.from_user.id == int(INITIAL_ADMIN_ID))
async def edit_main_menu(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    await state.clear()
    if not await check_rate_limit(user_id):
        lang = await get_user_language(user_id)
        await message.reply(translations[lang]["rate_limit"])
        return
    lang = await get_user_language(user_id)
    buttons = await redis_client.lrange(f"main_menu:{lang}", 0, -1)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=button, callback_data=f"edit_button_{i}")]
        for i, button in enumerate(buttons)
    ] + [
        [InlineKeyboardButton(text=translations[lang]["add_new_button"], callback_data="add_new_button")],
        [InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_admin")]
    ])
    await message.reply(translations[lang]["select_button_to_edit"], reply_markup=keyboard)
    await state.set_state(AdminStates.EDIT_BUTTONS)

@router.callback_query(StateFilter(AdminStates.EDIT_BUTTONS), F.data.startswith("edit_button_"))
async def process_button_selection(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = str(callback_query.from_user.id)
    lang = await get_user_language(user_id)
    button_index = int(callback_query.data.split("_")[-1])
    await state.update_data(button_index=button_index)
    await callback_query.message.edit_text(translations[lang]["enter_new_button_text"])
    await state.set_state(AdminStates.SET_BUTTON_TEXT)

@router.callback_query(StateFilter(AdminStates.EDIT_BUTTONS), F.data == "add_new_button")
async def add_new_button(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = str(callback_query.from_user.id)
    lang = await get_user_language(user_id)
    await state.update_data(action="add_button")
    await callback_query.message.edit_text(translations[lang]["enter_new_button_text"])
    await state.set_state(AdminStates.SET_BUTTON_TEXT)

@router.message(StateFilter(AdminStates.SET_BUTTON_TEXT))
async def process_button_text(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    lang = await get_user_language(user_id)
    data = await state.get_data()
    button_index = data.get("button_index")
    action = data.get("action")
    new_text = message.text.strip()
    
    if action == "add_button":
        await redis_client.rpush(f"main_menu:{lang}", new_text)
        await message.reply(translations[lang]["button_added"])
    else:
        buttons = await redis_client.lrange(f"main_menu:{lang}", 0, -1)
        if button_index < len(buttons):
            await redis_client.lset(f"main_menu:{lang}", button_index, new_text)
            await message.reply(translations[lang]["button_updated"])
        else:
            await message.reply(translations[lang]["invalid_button"])
            return
    await state.clear()

@router.message(F.text.in_([translations[lang]["manage_verifications"] for lang in translations]), F.from_user.id == int(INITIAL_ADMIN_ID))
async def manage_verifications(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    await state.clear()
    if not await check_rate_limit(user_id):
        lang = await get_user_language(user_id)
        await message.reply(translations[lang]["rate_limit"])
        return
    lang = await get_user_language(user_id)
    logger.info(f"User {user_id} entered manage_verifications")

    # دریافت درخواست‌های در انتظار از SQLite
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, timestamp, status FROM bank_cards WHERE status IN ("pending_user", "pending_admin")')
    pending_bank_cards = cursor.fetchall()
    cursor.execute('SELECT user_id, timestamp, status FROM receipts WHERE status IN ("pending_user", "pending_admin")')
    pending_receipts = cursor.fetchall()
    conn.close()
    logger.debug(f"Pending bank cards: {pending_bank_cards}, Pending receipts: {pending_receipts}")

    if not (pending_bank_cards or pending_receipts):
        await message.reply(
            translations[lang]["no_pending_verifications"],
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_admin")]
            ])
        )
        logger.debug(f"No pending verifications found for user {user_id}")
        return

    # حذف درخواست‌های تکراری برای هر کاربر (فقط آخرین درخواست نمایش داده شود)
    unique_bank_cards = {}
    for user_id, timestamp, status in pending_bank_cards:
        unique_bank_cards[user_id] = (timestamp, status)

    keyboard_rows = []
    for user_id, (timestamp, status) in unique_bank_cards.items():
        status_text = "در انتظار تأیید کاربر" if status == "pending_user" else "در انتظار تأیید ادمین"
        text = f"کارت بانکی: کاربر {user_id} ({status_text})"
        if status == "pending_user":
            text += " - منتظر تأیید کاربر"
        keyboard_rows.append([InlineKeyboardButton(
            text=text,
            callback_data=f"view_bank_card_{user_id}:{timestamp}"
        )])

    for user_id, timestamp, status in pending_receipts:
        status_text = "در انتظار تأیید کاربر" if status == "pending_user" else "در انتظار تأیید ادمین"
        text = f"فیش پرداخت: کاربر {user_id} ({status_text})"
        if status == "pending_user":
            text += " - منتظر تأیید کاربر"
        keyboard_rows.append([InlineKeyboardButton(
            text=text,
            callback_data=f"view_receipt_{user_id}:{timestamp}"
        )])

    keyboard_rows.append([InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_admin")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    await message.reply(translations[lang]["pending_verifications"], reply_markup=keyboard)
    await state.set_state(AdminStates.MANAGE_VERIFICATIONS)
    logger.debug(f"Displayed pending verifications for user {user_id}")
@router.callback_query(F.data.startswith("view_bank_card_"))
async def view_bank_card(callback_query: types.CallbackQuery, state: FSMContext):
    target_key = callback_query.data.replace("view_bank_card_", "")
    user_id, timestamp = target_key.split(":", 1)
    lang = await get_user_language(str(callback_query.from_user.id))
    
    bank_data = await get_bank_card_from_db(user_id, timestamp)
    if not bank_data:
        await callback_query.message.edit_text(translations[lang]["bank_card_photo_not_found"])
        return
    
    status_text = {
        "pending_user": "در انتظار تأیید کاربر",
        "pending_admin": "در انتظار تأیید ادمین",
        "approved": "تأیید شده",
        "rejected": "رد شده",
        "canceled": "لغو شده"
    }.get(bank_data["status"], "نامشخص")
    reject_reason = bank_data.get("reject_reason", "ندارد")
    
    # فقط برای کارت‌های در وضعیت pending_admin دکمه‌های تأیید/رد نمایش داده شوند
    keyboard_buttons = [[InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_verifications")]]
    if bank_data["status"] == "pending_admin":
        keyboard_buttons.insert(0, [
            InlineKeyboardButton(text=translations[lang]["approve_bank_card"], callback_data=f"approve_bank_card_{user_id}:{timestamp}"),
            InlineKeyboardButton(text=translations[lang]["reject_bank_card"], callback_data=f"reject_bank_card_{user_id}:{timestamp}")
        ])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    message_text = (
        translations[lang]["review_bank_card"].format(user_id=user_id) +
        f"\nشماره تلفن: {bank_data['phone_number']}\nوضعیت: {status_text}\nدلیل رد: {reject_reason}\nزمان: {timestamp}"
    )
    if bank_data["status"] == "pending_user":
        message_text += "\n(این کارت هنوز توسط کاربر تأیید نشده است و قابل تأیید یا رد نیست.)"
    
    await callback_query.message.edit_text(message_text, reply_markup=keyboard)
    if bank_data["photo_file_id"]:
        try:
            await bot.send_photo(user_id, photo=bank_data["photo_file_id"])
            logger.debug(f"Sent bank card photo to admin {user_id} for user {user_id}, timestamp: {timestamp}")
        except Exception as e:
            logger.error(f"Error sending bank card photo to admin {user_id}: {str(e)}")
            await callback_query.message.reply(translations[lang]["error_sending_photo"])
    logger.info(f"Displayed bank card details for user {user_id}, timestamp: {timestamp}")  
    
    
async def view_receipt(callback_query: types.CallbackQuery):
    user_id = str(callback_query.from_user.id)
    target_key = callback_query.data.replace("view_receipt_", "")
    target_user_id, timestamp = target_key.split(":", 1)
    lang = await get_user_language(user_id)
    
    receipt_data = await get_receipt_from_db(target_user_id, timestamp)
    if not receipt_data:
        await callback_query.message.edit_text(translations[lang]["receipt_photo_not_found"])
        return
    
    status_text = {
        "pending_user": "در انتظار تأیید کاربر",
        "pending_admin": "در انتظار تأیید ادمین",
        "approved": "تأیید شده",
        "rejected": "رد شده",
        "canceled": "لغو شده"
    }.get(receipt_data["status"], "نامشخص")
    reject_reason = receipt_data.get("reject_reason", "ندارد")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=translations[lang]["approve_receipt"], callback_data=f"approve_receipt_{target_user_id}:{timestamp}")],
        [InlineKeyboardButton(text=translations[lang]["reject_receipt"], callback_data=f"reject_receipt_{target_user_id}:{timestamp}")],
        [InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_admin")]
    ])
    await callback_query.message.edit_text(
        translations[lang]["review_receipt"].format(user_id=target_user_id) +
        f"\nنوع خرید: {receipt_data['purchase_type']}\nمبلغ: {receipt_data['price']:,} IRR\nدسته‌بندی: {receipt_data['plan_category']}\nوضعیت: {status_text}\nدلیل رد: {reject_reason}\nزمان: {timestamp}",
        reply_markup=keyboard
    )
    if receipt_data["photo_file_id"]:
        await bot.send_photo(user_id, photo=receipt_data["photo_file_id"])

@router.callback_query(F.data.startswith("approve_bank_card_"))
async def approve_bank_card(callback_query: types.CallbackQuery, state: FSMContext):
    target_key = callback_query.data.replace("approve_bank_card_", "")
    try:
        target_user_id, timestamp = target_key.split(":", 1)
    except ValueError:
        logger.error(f"Invalid callback data format: {target_key}")
        lang = await get_user_language(str(callback_query.from_user.id))
        await callback_query.message.edit_text(
            translations[lang]["invalid_action"],
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_verifications")]
            ])
        )
        return

    admin_lang = await get_user_language(str(callback_query.from_user.id))
    user_lang = await get_user_language(target_user_id)
    logger.info(f"START: Processing approve_bank_card for user {target_user_id}, timestamp: {timestamp}")

    try:
        # بررسی کارت در دیتابیس
        logger.debug(f"Fetching bank card data for {target_user_id}:{timestamp}")
        bank_data = await get_bank_card_from_db(target_user_id, timestamp)
        logger.debug(f"Bank card data for {target_user_id}:{timestamp}: {bank_data}")
        if not bank_data:
            logger.warning(f"No bank card found for user {target_user_id} at {timestamp}")
            await callback_query.message.edit_text(
                translations[admin_lang]["bank_card_photo_not_found"],
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=translations[admin_lang]["back"], callback_data="back_to_verifications")]
                ])
            )
            return

        # بررسی وضعیت کارت
        logger.debug(f"Checking status for bank card {target_user_id}:{timestamp}")
        if bank_data["status"] != "pending_admin":
            logger.warning(f"Bank card for {target_user_id}:{timestamp} is not in pending_admin status: {bank_data['status']}")
            await callback_query.message.edit_text(
                translations[admin_lang]["bank_card_not_pending_admin"],
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=translations[admin_lang]["back"], callback_data="back_to_verifications")]
                ])
            )
            return

        # تنظیم انقضا تا پایان روز
        logger.debug(f"Setting expiry for bank card")
        expiry = datetime.now().replace(hour=23, minute=59, second=59, microsecond=999999)
        expiry_jalali = jdatetime.fromgregorian(datetime=expiry).strftime("%Y/%m/%d %H:%M:%S")
        logger.debug(f"Set expiry for bank card: {expiry.isoformat()} (Jalali: {expiry_jalali})")

        # حذف درخواست‌های قدیمی‌تر در SQLite
        logger.debug(f"Deleting older pending bank cards for user {target_user_id}")
        try:
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute('DELETE FROM bank_cards WHERE user_id = ? AND timestamp != ? AND status IN ("pending_user", "pending_admin")', (target_user_id, timestamp))
            deleted_count = cursor.rowcount
            conn.commit()
            conn.close()
            logger.debug(f"Deleted {deleted_count} older pending bank cards for user {target_user_id}, keeping timestamp: {timestamp}")
        except sqlite3.Error as e:
            logger.error(f"SQLite error while deleting older bank cards for user {target_user_id}: {str(e)}")
            await callback_query.message.edit_text(
                translations[admin_lang]["error_occurred"],
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=translations[admin_lang]["back"], callback_data="back_to_verifications")]
                ])
            )
            return

        # به‌روزرسانی در SQLite
        logger.debug(f"Updating bank card status to 'approved' in SQLite for {target_user_id}:{timestamp}")
        try:
            await save_bank_card_to_db(
                target_user_id, timestamp, phone_number=bank_data["phone_number"],
                photo_file_id=bank_data["photo_file_id"], status="approved", expiry=expiry.isoformat()
            )
            logger.info(f"Bank card status updated to 'approved' in SQLite for {target_user_id}:{timestamp}")
        except sqlite3.Error as e:
            logger.error(f"SQLite error while updating bank card for user {target_user_id}: {str(e)}")
            await callback_query.message.edit_text(
                translations[admin_lang]["error_occurred"],
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=translations[admin_lang]["back"], callback_data="back_to_verifications")]
                ])
            )
            return

        # حذف درخواست‌های قدیمی‌تر در Redis
        logger.debug(f"Deleting older Redis keys for user {target_user_id}")
        try:
            redis_keys = await redis_client.hkeys("pending_bank_card_verifications")
            for key in redis_keys:
                if key.startswith(f"{target_user_id}:") and key != f"{target_user_id}:{timestamp}":
                    await redis_client.hdel("pending_bank_card_verifications", key)
                    logger.debug(f"Deleted Redis key {key} for user {target_user_id}")
        except Exception as e:
            logger.error(f"Redis error while deleting older bank card keys for user {target_user_id}: {str(e)}")
            await callback_query.message.edit_text(
                translations[admin_lang]["error_occurred"],
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=translations[admin_lang]["back"], callback_data="back_to_verifications")]
                ])
            )
            return

        # به‌روزرسانی در Redis
        logger.debug(f"Updating Redis for bank card {target_user_id}:{timestamp}")
        try:
            await redis_client.hset("pending_bank_card_verifications", f"{target_user_id}:{timestamp}", json.dumps({
                "photo_file_id": bank_data["photo_file_id"],
                "timestamp": timestamp,
                "phone_number": bank_data["phone_number"],
                "status": "approved",
                "expiry": expiry.isoformat()
            }))
            await redis_client.expire("pending_bank_card_verifications", 7200)
            await redis_client.set(f"user:{target_user_id}:bank_card_verified", "true", ex=7200)
            logger.debug(f"Redis updated for bank card {target_user_id}:{timestamp}")
        except Exception as e:
            logger.error(f"Redis error while updating bank card for user {target_user_id}: {str(e)}")
            await callback_query.message.edit_text(
                translations[admin_lang]["error_occurred"],
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=translations[admin_lang]["back"], callback_data="back_to_verifications")]
                ])
            )
            return

        # اطلاع‌رسانی به کاربر
        logger.debug(f"Sending approval notification to user {target_user_id}")
        try:
            await bot.send_photo(
                target_user_id,
                photo=bank_data["photo_file_id"],
                caption=translations[user_lang]["bank_card_approved_until"].format(expiry=expiry_jalali)
            )
            logger.info(f"Sent approval notification with photo to user {target_user_id}")
        except Exception as e:
            logger.error(f"Error sending bank card photo to {target_user_id}: {str(e)}")
            await bot.send_message(
                target_user_id,
                translations[user_lang]["bank_card_approved_until"].format(expiry=expiry_jalali)
            )
            logger.info(f"Sent approval notification without photo to user {target_user_id}")

        # بررسی خرید در انتظار
        logger.debug(f"Checking pending purchase for user {target_user_id}")
        pending_purchase = await redis_client.get(f"user:{target_user_id}:pending_purchase")
        logger.debug(f"Pending purchase for user {target_user_id}: {pending_purchase}")
        if pending_purchase:
            purchase_data = json.loads(pending_purchase)
            price = purchase_data.get("price", 0)
            logger.debug(f"Sending payment instructions to user {target_user_id} for amount {price}")
            try:
                await bot.send_message(
                    target_user_id,
                    translations[user_lang]["payment_instructions"].format(
                        card_number=BANK_CARD_NUMBER,
                        amount=price
                    )
                )
                await bot.send_message(target_user_id, translations[user_lang]["send_receipt_photo"])
                await state.set_state(UserStates.VERIFY_RECEIPT)
                await state.update_data(**purchase_data)
                logger.debug(f"Set state to VERIFY_RECEIPT for user {target_user_id}")
            except Exception as e:
                logger.error(f"Error sending payment instructions to {target_user_id}: {str(e)}")
                await callback_query.message.edit_text(
                    translations[admin_lang]["error_occurred"],
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text=translations[admin_lang]["back"], callback_data="back_to_verifications")]
                    ])
                )
                return
        else:
            logger.debug(f"No pending purchase found for user {target_user_id}, prompting to retry")
            try:
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=translations[user_lang]["buy_premium"], callback_data="retry_premium")],
                    [InlineKeyboardButton(text=translations[user_lang]["buy_stars"], callback_data="retry_stars")]
                ])
                await bot.send_message(target_user_id, translations[user_lang]["retry_purchase"], reply_markup=keyboard)
                logger.debug(f"Sent retry purchase message to user {target_user_id}")
            except Exception as e:
                logger.error(f"Error sending retry purchase message to {target_user_id}: {str(e)}")
                await callback_query.message.edit_text(
                    translations[admin_lang]["error_occurred"],
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text=translations[admin_lang]["back"], callback_data="back_to_verifications")]
                    ])
                )
                return

        # اطلاع‌رسانی به ادمین
        logger.debug(f"Sending approval confirmation to admin {callback_query.from_user.id}")
        try:
            await callback_query.message.edit_text(
                text=translations[admin_lang]["bank_card_approved_admin"].format(user_id=target_user_id),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=translations[admin_lang]["back"], callback_data="back_to_verifications")]
                ])
            )
            logger.debug(f"Updated message for admin {callback_query.from_user.id} with approval confirmation")
        except Exception as e:
            logger.error(f"Error editing message for admin {callback_query.from_user.id}: {str(e)}")
            await callback_query.message.reply(
                translations[admin_lang]["bank_card_approved_admin"].format(user_id=target_user_id),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=translations[admin_lang]["back"], callback_data="back_to_verifications")]
                ])
            )
            logger.debug(f"Sent new message for admin {callback_query.from_user.id} with approval confirmation")

        logger.info(f"COMPLETED: Bank card approved for user {target_user_id} at {timestamp}")

    except Exception as e:
        logger.error(f"Unexpected error in approve_bank_card for user {target_user_id}, timestamp {timestamp}: {str(e)}")
        await callback_query.message.edit_text(
            translations[admin_lang]["error_occurred"],
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=translations[admin_lang]["back"], callback_data="back_to_verifications")]
            ])
        )







@router.callback_query(StateFilter(UserStates.PURCHASE_TYPE), F.data.in_(["continue_with_current_card", "verify_new_card"]))
async def handle_card_choice(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = str(callback_query.from_user.id)
    lang = await get_user_language(user_id)
    data = await state.get_data()
    plan_category = data.get("plan_category", "premium")
    photo_file_id = data.get("photo_file_id")

    if callback_query.data == "continue_with_current_card":
        # نمایش منوی انتخاب پلن
        if plan_category == "premium":
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"{plan} - {price:,} IRR", callback_data=f"premium_{plan}")]
                for plan, price in PREMIUM_PRICES.items()
            ] + [[InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_main")]])
        else:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"{plan} - {price:,} IRR", callback_data=f"stars_{plan}")]
                for plan, price in STARS_PRICES.items()
            ] + [[InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_main")]])
        await callback_query.message.edit_text(translations[lang]["select_plan"], reply_markup=keyboard)
        await state.update_data(is_bank_verified=True)
        await state.set_state(UserStates.PURCHASE_TYPE)

    elif callback_query.data == "verify_new_card":
        # ابطال کارت‌های تأییدشده قبلی
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('UPDATE bank_cards SET status = "canceled", reject_reason = "کارت جدید ثبت شد" WHERE user_id = ? AND status = "approved"', (user_id,))
        conn.commit()
        conn.close()
        logger.debug(f"Canceled previous approved bank cards for user {user_id}")

        # حذف کارت‌های تأییدشده از Redis
        redis_keys = await redis_client.hkeys("pending_bank_card_verifications")
        for key in redis_keys:
            if key.startswith(f"{user_id}:"):
                card_data = json.loads(await redis_client.hget("pending_bank_card_verifications", key))
                if card_data.get("status") == "approved":
                    await redis_client.hdel("pending_bank_card_verifications", key)
                    logger.debug(f"Deleted approved Redis key {key} for user {user_id}")
                    await callback_query.message.edit_text(translations[lang]["verification_message"])
                    await state.update_data(is_bank_verified=False)
                    await state.set_state(UserStates.VERIFY_BANK_CARD)

@router.callback_query(F.data.startswith("reject_bank_card_"))
async def reject_bank_card(callback_query: types.CallbackQuery, state: FSMContext):
    target_key = callback_query.data.replace("reject_bank_card_", "")
    try:
        target_user_id, timestamp = target_key.split(":", 1)
    except ValueError:
        lang = await get_user_language(str(callback_query.from_user.id))
        await callback_query.message.edit_text(translations[lang]["invalid_action"])
        return
    
    admin_lang = await get_user_language(str(callback_query.from_user.id))
    user_lang = await get_user_language(target_user_id)
    
    # به‌روزرسانی در SQLite
    await save_bank_card_to_db(target_user_id, timestamp, status="rejected")
    
    # به‌روزرسانی در Redis
    bank_data = await get_bank_card_from_db(target_user_id, timestamp)
    if bank_data:
        await redis_client.hset("pending_bank_card_verifications", f"{target_user_id}:{timestamp}", json.dumps({
            "photo_file_id": bank_data["photo_file_id"],
            "timestamp": timestamp,
            "phone_number": bank_data["phone_number"],
            "status": "rejected",  # اصلاح شده
            "reject_reason": "در انتظار دلیل رد"
        }))
        await redis_client.expire("pending_bank_card_verifications", 3600)
    
    # انتقال به حالت وارد کردن دلیل رد
    await state.set_state(AdminStates.ENTER_REJECT_REASON)
    await state.update_data(target_user_id=target_user_id, timestamp=timestamp, type="bank_card")
    await callback_query.message.edit_text(translations[admin_lang]["enter_reject_reason"].format(user_id=target_user_id))
@router.callback_query(F.data.startswith("approve_receipt_"))
async def approve_receipt(callback_query: types.CallbackQuery, state: FSMContext):
    logger.info(f"Approve receipt callback triggered by admin {callback_query.from_user.id} with data {callback_query.data}")
    target_key = callback_query.data.replace("approve_receipt_", "")
    try:
        target_user_id, timestamp = target_key.split(":", 1)
    except ValueError:
        logger.error(f"Invalid callback data format: {target_key}")
        admin_lang = await get_user_language(str(callback_query.from_user.id))
        await callback_query.message.edit_text(
            translations[admin_lang]["invalid_action"],
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=translations[admin_lang]["back"], callback_data="back_to_verifications")]
            ])
        )
        return

    admin_lang = await get_user_language(str(callback_query.from_user.id))
    user_lang = await get_user_language(target_user_id)
    logger.debug(f"Processing approve_receipt for user {target_user_id}, timestamp: {timestamp}")

    try:
        # بررسی فیش در دیتابیس
        receipt_data = await get_receipt_from_db(target_user_id, timestamp)
        logger.debug(f"Receipt data for {target_user_id}:{timestamp}: {receipt_data}")
        if not receipt_data:
            logger.warning(f"No receipt found for user {target_user_id} at {timestamp}")
            await callback_query.message.edit_text(
                translations[admin_lang]["receipt_photo_not_found"],
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=translations[admin_lang]["back"], callback_data="back_to_verifications")]
                ])
            )
            return

        # بررسی وضعیت فیش
        if receipt_data["status"] != "pending_admin":
            logger.warning(f"Receipt for {target_user_id}:{timestamp} is not in pending_admin status: {receipt_data['status']}")
            await callback_query.message.edit_text(
                translations[admin_lang]["receipt_not_pending_admin"],
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=translations[admin_lang]["back"], callback_data="back_to_verifications")]
                ])
            )
            return

        # به‌روزرسانی وضعیت فیش به تأیید شده در SQLite
        logger.debug(f"Updating receipt status to 'approved' in SQLite for {target_user_id}:{timestamp}")
        await save_receipt_to_db(
            target_user_id, timestamp, receipt_data["purchase_type"],
            receipt_data["price"], receipt_data["plan_category"], status="approved",
            photo_file_id=receipt_data["photo_file_id"]
        )
        logger.info(f"Receipt status updated to 'approved' in SQLite for {target_user_id}:{timestamp}")

        # به‌روزرسانی در Redis
        logger.debug(f"Updating receipt status in Redis for {target_user_id}:{timestamp}")
        await redis_client.hset("pending_receipt_verifications", f"{target_user_id}:{timestamp}", json.dumps({
            "photo_file_id": receipt_data["photo_file_id"],
            "timestamp": timestamp,
            "user_id": target_user_id,
            "status": "approved",
            "purchase_type": receipt_data["purchase_type"],
            "price": receipt_data["price"],
            "plan_category": receipt_data["plan_category"]
        }))
        await redis_client.expire("pending_receipt_verifications", 7200)
        logger.debug(f"Redis updated for receipt {target_user_id}:{timestamp}")

        # بررسی خرید در انتظار
        pending_purchase = await redis_client.get(f"user:{target_user_id}:pending_purchase")
        logger.debug(f"Pending purchase for user {target_user_id}: {pending_purchase}")
        if not pending_purchase:
            logger.warning(f"No pending purchase found for user {target_user_id}, notifying to retry")
            await bot.send_message(
                target_user_id,
                translations[user_lang]["no_pending_purchase"],
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=translations[user_lang]["buy_premium"], callback_data="retry_premium")],
                    [InlineKeyboardButton(text=translations[user_lang]["buy_stars"], callback_data="retry_stars")]
                ])
            )
        else:
            purchase_data = json.loads(pending_purchase)
            purchase_type = purchase_data.get("purchase_type", "unknown")
            target_id = purchase_data.get("target_id", target_user_id)
            plan_category = purchase_data.get("plan_category", "premium")
            price = purchase_data.get("price", 0)

            # ذخیره سفارش با وضعیت "pending" در SQLite
            logger.debug(f"Saving order with status 'pending' for {target_user_id}:{timestamp}")
            await save_order_to_db(
                target_user_id, timestamp, purchase_type, price, plan_category, target_id, status="pending"
            )
            logger.info(f"Order saved with status 'pending' for {target_user_id}:{timestamp}")

            # حذف خرید در انتظار از Redis
            logger.debug(f"Deleting pending purchase from Redis for user {target_user_id}")
            deleted = await redis_client.delete(f"user:{target_user_id}:pending_purchase")
            logger.debug(f"Deleted {deleted} pending purchase entries for user {target_user_id}")

            # اطلاع‌رسانی به کاربر درباره سفارش در انتظار
            logger.debug(f"Notifying user {target_user_id} about pending order")
            await bot.send_message(
                target_user_id,
                translations[user_lang]["order_pending_notification"],
                reply_markup=await get_main_menu(user_lang, target_user_id)
            )

        # حذف فیش از Redis
        logger.debug(f"Deleting receipt from pending_receipt_verifications for {target_user_id}:{timestamp}")
        await redis_client.hdel("pending_receipt_verifications", f"{target_user_id}:{timestamp}")

        # اطلاع‌رسانی به کاربر درباره تأیید فیش
        logger.debug(f"Notifying user {target_user_id} about receipt approval")
        await bot.send_message(
            target_user_id,
            translations[user_lang]["receipt_approved"],
            reply_markup=await get_main_menu(user_lang, target_user_id)
        )

        # اطلاع‌رسانی به ادمین
        logger.debug(f"Notifying admin about receipt approval for {target_user_id}")
        try:
            if callback_query.message.caption:
                await callback_query.message.edit_caption(
                    caption=translations[admin_lang]["receipt_approved_admin"].format(user_id=target_user_id),
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text=translations[admin_lang]["back"], callback_data="back_to_verifications")]
                    ])
                )
            else:
                await callback_query.message.edit_text(
                    text=translations[admin_lang]["receipt_approved_admin"].format(user_id=target_user_id),
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text=translations[admin_lang]["back"], callback_data="back_to_verifications")]
                    ])
                )
        except Exception as e:
            logger.error(f"Error editing message for admin {callback_query.from_user.id}: {str(e)}")
            await callback_query.message.reply(
                translations[admin_lang]["receipt_approved_admin"].format(user_id=target_user_id),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=translations[admin_lang]["back"], callback_data="back_to_verifications")]
                ])
            )

        logger.info(f"Receipt approved for user {target_user_id} at {timestamp}, order set to pending")

    except Exception as e:
        logger.error(f"Error in approve_receipt for user {target_user_id}, timestamp {timestamp}: {str(e)}")
        await callback_query.message.edit_text(
            translations[admin_lang]["error_occurred"],
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=translations[admin_lang]["back"], callback_data="back_to_verifications")]
            ])
        )

@router.callback_query(F.data.startswith("reject_receipt_"))
async def reject_receipt(callback_query: types.CallbackQuery, state: FSMContext):
    logger.info(f"Reject receipt callback triggered by admin {callback_query.from_user.id} with data {callback_query.data}")
    target_key = callback_query.data.replace("reject_receipt_", "")
    try:
        target_user_id, timestamp = target_key.split(":", 1)
    except ValueError:
        logger.error(f"Invalid callback data format: {target_key}")
        admin_lang = await get_user_language(str(callback_query.from_user.id))
        await callback_query.message.edit_text(
            translations[admin_lang]["invalid_action"],
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=translations[admin_lang]["back"], callback_data="back_to_verifications")]
            ])
        )
        return

    admin_lang = await get_user_language(str(callback_query.from_user.id))
    user_lang = await get_user_language(target_user_id)
    logger.debug(f"Processing reject_receipt for user {target_user_id}, timestamp: {timestamp}")

    try:
        # بررسی فیش در دیتابیس
        receipt_data = await get_receipt_from_db(target_user_id, timestamp)
        logger.debug(f"Receipt data for {target_user_id}:{timestamp}: {receipt_data}")
        if not receipt_data:
            logger.warning(f"No receipt found for user {target_user_id} at {timestamp}")
            await callback_query.message.edit_text(
                translations[admin_lang]["receipt_photo_not_found"],
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=translations[admin_lang]["back"], callback_data="back_to_verifications")]
                ])
            )
            return

        # به‌روزرسانی در SQLite
        logger.debug(f"Updating receipt status to 'rejected' in SQLite for {target_user_id}:{timestamp}")
        await save_receipt_to_db(
            target_user_id, timestamp, receipt_data["purchase_type"],
            receipt_data["price"], receipt_data["plan_category"], status="rejected",
            photo_file_id=receipt_data["photo_file_id"]
        )
        logger.info(f"Receipt status updated to 'rejected' in SQLite for {target_user_id}:{timestamp}")

        # به‌روزرسانی در Redis
        logger.debug(f"Updating receipt status in Redis for {target_user_id}:{timestamp}")
        await redis_client.hset("pending_receipt_verifications", f"{target_user_id}:{timestamp}", json.dumps({
            "photo_file_id": receipt_data["photo_file_id"],
            "timestamp": timestamp,
            "user_id": target_user_id,
            "status": "rejected",
            "purchase_type": receipt_data["purchase_type"],
            "price": receipt_data["price"],
            "plan_category": receipt_data["plan_category"],
            "reject_reason": "در انتظار دلیل رد"
        }))
        await redis_client.expire("pending_receipt_verifications", 7200)
        logger.debug(f"Redis updated for receipt {target_user_id}:{timestamp}")

        # حذف خرید در انتظار از Redis
        logger.debug(f"Deleting pending purchase from Redis for user {target_user_id}")
        deleted = await redis_client.delete(f"user:{target_user_id}:pending_purchase")
        logger.debug(f"Deleted {deleted} pending purchase entries for user {target_user_id}")

        # انتقال به حالت وارد کردن دلیل رد
        await state.set_state(AdminStates.ENTER_REJECT_REASON)
        await state.update_data(target_user_id=target_user_id, timestamp=timestamp, type="receipt")
        try:
            if callback_query.message.caption:
                await callback_query.message.edit_caption(
                    caption=translations[admin_lang]["enter_reject_reason"].format(user_id=target_user_id),
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text=translations[admin_lang]["back"], callback_data="back_to_verifications")]
                    ])
                )
            else:
                await callback_query.message.edit_text(
                    text=translations[admin_lang]["enter_reject_reason"].format(user_id=target_user_id),
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text=translations[admin_lang]["back"], callback_data="back_to_verifications")]
                    ])
                )
        except Exception as e:
            logger.error(f"Error editing message for admin {callback_query.from_user.id}: {str(e)}")
            await callback_query.message.reply(
                translations[admin_lang]["enter_reject_reason"].format(user_id=target_user_id),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=translations[admin_lang]["back"], callback_data="back_to_verifications")]
                ])
            )
        logger.info(f"Receipt rejection initiated for user {target_user_id} at {timestamp}, awaiting reject reason")

    except Exception as e:
        logger.error(f"Error in reject_receipt for user {target_user_id}, timestamp {timestamp}: {str(e)}")
        await callback_query.message.reply(
            translations[admin_lang]["error_occurred"],
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=translations[admin_lang]["back"], callback_data="back_to_verifications")]
            ])
        )

@router.callback_query(F.data == "back_to_verifications")
async def back_to_verifications(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = str(callback_query.from_user.id)
    lang = await get_user_language(user_id)
    logger.debug(f"User {user_id} navigating back to verifications")

    # دریافت درخواست‌های در انتظار از SQLite
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, timestamp, status FROM bank_cards WHERE status IN ("pending_user", "pending_admin")')
    pending_bank_cards = cursor.fetchall()
    cursor.execute('SELECT user_id, timestamp, status FROM receipts WHERE status IN ("pending_user", "pending_admin")')
    pending_receipts = cursor.fetchall()
    conn.close()
    logger.debug(f"Pending bank cards: {pending_bank_cards}, Pending receipts: {pending_receipts}")

    if not (pending_bank_cards or pending_receipts):
        await callback_query.message.edit_text(
            translations[lang]["no_pending_verifications"],
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_admin")]
            ])
        )
        logger.debug(f"No pending verifications found for user {user_id}")
        return

    unique_bank_cards = {}
    for user_id, timestamp, status in pending_bank_cards:
        unique_bank_cards[user_id] = (timestamp, status)

    keyboard_rows = []
    for user_id, (timestamp, status) in unique_bank_cards.items():
        status_text = "در انتظار تأیید کاربر" if status == "pending_user" else "در انتظار تأیید ادمین"
        keyboard_rows.append([InlineKeyboardButton(
            text=f"کارت بانکی: کاربر {user_id} ({status_text})",
            callback_data=f"view_bank_card_{user_id}:{timestamp}"
        )])

    for user_id, timestamp, status in pending_receipts:
        status_text = "در انتظار تأیید کاربر" if status == "pending_user" else "در انتظار تأیید ادمین"
        keyboard_rows.append([InlineKeyboardButton(
            text=f"فیش پرداخت: کاربر {user_id} ({status_text})",
            callback_data=f"view_receipt_{user_id}:{timestamp}"
        )])

    keyboard_rows.append([InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_admin")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    await callback_query.message.edit_text(
        translations[lang]["pending_verifications"],
        reply_markup=keyboard
    )
    await state.set_state(AdminStates.MANAGE_VERIFICATIONS)
    logger.info(f"Returned to verifications for user {user_id}")

@router.message(StateFilter(AdminStates.ENTER_REJECT_REASON))
async def process_reject_reason(message: types.Message, state: FSMContext):
    admin_id = str(message.from_user.id)
    admin_lang = await get_user_language(admin_id)
    data = await state.get_data()
    target_user_id = data.get("target_user_id")
    timestamp = data.get("timestamp")
    type_ = data.get("type")
    reject_reason = message.text.strip()
    
    user_lang = await get_user_language(target_user_id)
    
    if type_ == "bank_card":
        await save_bank_card_to_db(target_user_id, timestamp, status="rejected", reject_reason=reject_reason)
        bank_data = await get_bank_card_from_db(target_user_id, timestamp)
        if bank_data:
            await redis_client.hset("pending_bank_card_verifications", f"{target_user_id}:{timestamp}", json.dumps({
                "photo_file_id": bank_data["photo_file_id"],
                "timestamp": timestamp,
                "phone_number": bank_data["phone_number"],
                "status": "rejected",
                "reject_reason": reject_reason
            }))
            await redis_client.expire("pending_bank_card_verifications", 7200)
        await bot.send_message(
            target_user_id,
            translations[user_lang].get("bank_card_rejected_with_reason", "کارت بانکی شما به دلیل {reason} رد شد.").format(reason=reject_reason)
        )
    elif type_ == "receipt":
        receipt_data = await get_receipt_from_db(target_user_id, timestamp)
        if receipt_data:
            await save_receipt_to_db(
                target_user_id, timestamp, receipt_data["purchase_type"],
                receipt_data["price"], receipt_data["plan_category"], status="rejected",
                photo_file_id=receipt_data["photo_file_id"], reject_reason=reject_reason
            )
            await redis_client.hset("pending_receipt_verifications", f"{target_user_id}:{timestamp}", json.dumps({
                "photo_file_id": receipt_data["photo_file_id"],
                "timestamp": timestamp,
                "user_id": target_user_id,
                "status": "rejected",
                "purchase_type": receipt_data["purchase_type"],
                "price": receipt_data["price"],
                "plan_category": receipt_data["plan_category"],
                "reject_reason": reject_reason
            }))
            await redis_client.expire("pending_receipt_verifications", 7200)
            # ارسال پیام دلیل رد
            await bot.send_message(
                target_user_id,
                translations[user_lang].get("receipt_rejected_with_reason", "فیش پرداخت شما به دلیل {reason} رد شد.").format(reason=reject_reason)
            )
            # ارسال پیام اضافی برای پشتیبانی
            await bot.send_message(
                target_user_id,
                translations[user_lang].get("support_contact_soon", "به زودی پشتیبانی با شما ارتباط برقرار خواهد کرد برای بازگرداندن مبلغ پرداخت شده.")
            )
    
    await message.reply(translations[admin_lang]["reject_reason_sent"].format(user_id=target_user_id))
    await state.clear()
async def cancel_admin(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = str(callback_query.from_user.id)
    lang = await get_user_language(user_id)
    await callback_query.message.delete()
    await callback_query.message.answer(translations[lang]["admin_panel"], reply_markup=await get_admin_menu(lang))
    await state.clear()
@router.message(F.text.in_([translations[lang]["buy_premium"] for lang in translations]))
async def buy_premium(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    await state.clear()
    if not await check_rate_limit(user_id):
        lang = await get_user_language(user_id)
        await message.reply(translations[lang]["rate_limit"])
        return
    lang = await get_user_language(user_id)
    logger.info(f"User {user_id} entered buy_premium")

    # بررسی کارت تأییدشده
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT expiry, photo_file_id FROM bank_cards WHERE user_id = ? AND status = "approved" ORDER BY timestamp DESC LIMIT 1', (user_id,))
    approved_card = cursor.fetchone()
    conn.close()
    logger.debug(f"Approved card check for user {user_id}: {approved_card}")

    is_bank_verified = False
    photo_file_id = None
    expiry_dt = None
    if approved_card and approved_card[0]:
        expiry_dt = datetime.fromisoformat(approved_card[0])
        photo_file_id = approved_card[1]
        is_bank_verified = datetime.now() <= expiry_dt
        logger.debug(f"Bank card status for user {user_id}: verified={is_bank_verified}, expiry={approved_card[0]}")

    if is_bank_verified:
        # تبدیل تاریخ انقضا به شمسی
        expiry_jalali = jdatetime.fromgregorian(datetime=expiry_dt).strftime("%Y/%m/%d %H:%M:%S")
        # ارسال تصویر کارت تأییدشده
        try:
            await bot.send_photo(
                user_id,
                photo=photo_file_id,
                caption=translations[lang]["bank_card_approved_until"].format(expiry=expiry_jalali)
            )
        except Exception as e:
            logger.error(f"Error sending bank card photo to {user_id}: {e}")
            await message.reply(translations[lang]["error_sending_photo"])

        # ارائه گزینه‌های ادامه با کارت فعلی یا ثبت کارت جدید
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=translations[lang]["continue_with_current_card"], callback_data="continue_with_current_card")],
            [InlineKeyboardButton(text=translations[lang]["verify_new_card"], callback_data="verify_new_card")],
            [InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_main")]
        ])
        await message.reply(translations[lang]["card_already_verified"], reply_markup=keyboard)
        await state.update_data(plan_category="premium", is_bank_verified=True, photo_file_id=photo_file_id)
        await state.set_state(UserStates.PURCHASE_TYPE)
    else:
        # نمایش منوی انتخاب پلن
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"{plan} - {price:,} IRR", callback_data=f"premium_{plan}")]
            for plan, price in PREMIUM_PRICES.items()
        ] + [[InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_main")]])
        await state.update_data(plan_category="premium", is_bank_verified=False)
        await message.reply(translations[lang]["select_plan"], reply_markup=keyboard)
        await state.set_state(UserStates.PURCHASE_TYPE)
    logger.debug(f"State set to PURCHASE_TYPE for user {user_id}, plan_category=premium, is_bank_verified={is_bank_verified}")
    
    
@router.message(F.text.in_([translations[lang]["buy_stars"] for lang in translations]))
async def buy_stars(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    await state.clear()
    if not await check_rate_limit(user_id):
        lang = await get_user_language(user_id)
        await message.reply(translations[lang]["rate_limit"])
        return
    lang = await get_user_language(user_id)
    logger.info(f"User {user_id} entered buy_stars")

    # بررسی کارت تأییدشده
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT expiry, photo_file_id FROM bank_cards WHERE user_id = ? AND status = "approved" ORDER BY timestamp DESC LIMIT 1', (user_id,))
    approved_card = cursor.fetchone()
    conn.close()
    logger.debug(f"Approved card check for user {user_id}: {approved_card}")

    is_bank_verified = False
    photo_file_id = None
    expiry_dt = None
    if approved_card and approved_card[0]:
        expiry_dt = datetime.fromisoformat(approved_card[0])
        photo_file_id = approved_card[1]
        is_bank_verified = datetime.now() <= expiry_dt
        logger.debug(f"Bank card status for user {user_id}: verified={is_bank_verified}, expiry={approved_card[0]}")

    if is_bank_verified:
        # تبدیل تاریخ انقضا به شمسی
        expiry_jalali = jdatetime.fromgregorian(datetime=expiry_dt).strftime("%Y/%m/%d %H:%M:%S")
        # ارسال تصویر کارت تأییدشده
        try:
            await bot.send_photo(
                user_id,
                photo=photo_file_id,
                caption=translations[lang]["bank_card_approved_until"].format(expiry=expiry_jalali)
            )
        except Exception as e:
            logger.error(f"Error sending bank card photo to {user_id}: {e}")
            await message.reply(translations[lang]["error_sending_photo"])

        # ارائه گزینه‌های ادامه با کارت فعلی یا ثبت کارت جدید
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=translations[lang]["continue_with_current_card"], callback_data="continue_with_current_card")],
            [InlineKeyboardButton(text=translations[lang]["verify_new_card"], callback_data="verify_new_card")],
            [InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_main")]
        ])
        await message.reply(translations[lang]["card_already_verified"], reply_markup=keyboard)
        await state.update_data(plan_category="stars", is_bank_verified=True, photo_file_id=photo_file_id)
        await state.set_state(UserStates.PURCHASE_TYPE)
    else:
        # نمایش منوی انتخاب پلن
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"{plan} - {price:,} IRR", callback_data=f"stars_{plan}")]
            for plan, price in STARS_PRICES.items()
        ] + [[InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_main")]])
        await state.update_data(plan_category="stars", is_bank_verified=False)
        await message.reply(translations[lang]["select_plan"], reply_markup=keyboard)
        await state.set_state(UserStates.PURCHASE_TYPE)
    logger.debug(f"State set to PURCHASE_TYPE for user {user_id}, plan_category=stars, is_bank_verified={is_bank_verified}")
    
    
@router.callback_query(F.data == "back_to_main")
async def back_to_main_menu(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = str(callback_query.from_user.id)
    lang = await get_user_language(user_id)
    await callback_query.message.delete()
    user_name = callback_query.from_user.first_name or callback_query.from_user.username or user_id
    await callback_query.message.answer(translations[lang]["welcome"].format(name=user_name), reply_markup=await get_main_menu(lang, user_id))
    await state.clear()

@router.callback_query(F.data == "back_to_purchase_type")
async def back_to_purchase_type(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = str(callback_query.from_user.id)
    lang = await get_user_language(user_id)
    data = await state.get_data()
    plan_category = data.get("plan_category", "premium")
    
    if plan_category == "premium":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"{plan} - {price:,} IRR", callback_data=f"premium_{plan}")]
            for plan, price in PREMIUM_PRICES.items()
        ] + [[InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_main")]])
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"{plan} - {price:,} IRR", callback_data=f"stars_{plan}")]
            for plan, price in STARS_PRICES.items()
        ] + [[InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_main")]])
    await callback_query.message.edit_text(translations[lang]["select_plan"], reply_markup=keyboard)
    await state.set_state(UserStates.PURCHASE_TYPE)

@router.callback_query(StateFilter(UserStates.PURCHASE_TYPE), F.data.regexp(r"^(premium|stars)_"))
async def handle_plan_selection(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = str(callback_query.from_user.id)
    lang = await get_user_language(user_id)
    purchase_type = callback_query.data
    logger.debug(f"Processing plan selection: {purchase_type} for user {user_id}")
    logger.debug(f"Current PREMIUM_PRICES: {PREMIUM_PRICES}, STARS_PRICES: {STARS_PRICES}")
    
    plan_name = purchase_type.split("_", 1)[1]
    logger.debug(f"Extracted plan_name: {plan_name}")
    
    if purchase_type.startswith("premium_"):
        price = PREMIUM_PRICES.get(plan_name)
        if not price:
            logger.error(f"Plan {plan_name} not found in PREMIUM_PRICES")
            await callback_query.message.edit_text(translations[lang]["plan_not_available"])
            return
    else:
        price = STARS_PRICES.get(plan_name)
        if not price:
            logger.error(f"Plan {plan_name} not found in STARS_PRICES")
            await callback_query.message.edit_text(translations[lang]["plan_not_available"])
            return
    
    data = await state.get_data()
    plan_category = data.get("plan_category", "stars")
    is_bank_verified = data.get("is_bank_verified", False)
    logger.debug(f"Plan category: {plan_category}, is_bank_verified: {is_bank_verified}, State data: {data}")
    
    # ذخیره اطلاعات خرید در انتظار
    pending_data = {
        "purchase_type": purchase_type,
        "price": price,
        "plan_category": plan_category
    }
    try:
        await redis_client.set(f"user:{user_id}:pending_purchase", json.dumps(pending_data), ex=7200)
        logger.info(f"Pending purchase saved for user {user_id}: {pending_data}")
    except Exception as e:
        logger.error(f"Error saving pending purchase for user {user_id}: {str(e)}")
        await callback_query.message.edit_text(translations[lang]["error_occurred"])
        await state.clear()
        return
    
    await state.update_data(
        purchase_type=purchase_type,
        price=price,
        plan_category=plan_category,
        is_bank_verified=is_bank_verified
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=translations[lang]["for_myself"], callback_data="for_myself")],
        [InlineKeyboardButton(text=translations[lang]["for_others"], callback_data="for_others")],
        [InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_purchase_type")]
    ])
    
    await callback_query.message.edit_text(
        translations[lang]["purchase_for"].format(type=plan_name, price=price),
        reply_markup=keyboard
    )
    await state.set_state(UserStates.PURCHASE_FOR) 
    
@router.callback_query(StateFilter(UserStates.PURCHASE_FOR), F.data.in_(["for_myself", "for_others"]))
async def process_purchase_for(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = str(callback_query.from_user.id)
    lang = await get_user_language(user_id)
    data = await state.get_data()
    purchase_type = data["purchase_type"]
    price = (PREMIUM_PRICES.get(purchase_type.split("_")[1]) if purchase_type.startswith("premium_")
             else STARS_PRICES.get(purchase_type.split("_")[1]))
    is_bank_verified = data.get("is_bank_verified", False)
    logger.debug(f"Processing purchase for user {user_id}, purchase_type: {purchase_type}, is_bank_verified: {is_bank_verified}")

    if callback_query.data == "for_myself":
        user_name = callback_query.from_user.username or callback_query.from_user.first_name or user_id
        await state.update_data(target_id=user_id, target_name=user_name)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=translations[lang]["confirm"], callback_data="confirm_purchase")],
            [InlineKeyboardButton(text=translations[lang]["cancel"], callback_data="cancel_purchase")],
            [InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_purchase_type")]
        ])
        await callback_query.message.edit_text(
            translations[lang]["confirm_purchase"].format(type=purchase_type, target=user_name, price=price),
            reply_markup=keyboard
        )
        await state.set_state(UserStates.PURCHASE_CONFIRM)
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=translations[lang]["cancel"], callback_data="cancel_purchase")],
            [InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_purchase_type")]
        ])
        await callback_query.message.edit_text(
            translations[lang]["enter_other_phone"],
            reply_markup=keyboard
        )
        await state.set_state(UserStates.ENTER_OTHER_PHONE)  
        



@router.message(StateFilter(UserStates.ENTER_OTHER_PHONE))
async def process_other_phone(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    lang = await get_user_language(user_id)
    input_text = message.text.strip()
    
    target_id = None
    target_name = None
    if input_text.startswith("@"):
        username = input_text[1:]
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getChat?chat_id=@{username}") as resp:
                    result = await resp.json()
                    if result.get("ok"):
                        target_id = str(result["result"]["id"])
                        target_name = result["result"].get("username", result["result"].get("first_name", target_id))
                    else:
                        await message.reply(translations[lang]["user_not_found"], reply_markup=await get_main_menu(lang, user_id))
                        return
        except aiohttp.ClientError as e:
            logger.error(f"Error fetching user info for {username}: {e}")
            await message.reply(translations[lang]["error_occurred"], reply_markup=await get_main_menu(lang, user_id))
            return
    elif input_text.isdigit():
        target_id = input_text
        try:
            user_info = await bot.get_chat(target_id)
            target_name = user_info.username or user_info.first_name or target_id
        except Exception as e:
            logger.error(f"Error fetching user info for ID {target_id}: {e}")
            await message.reply(translations[lang]["user_not_found"], reply_markup=await get_main_menu(lang, user_id))
            return
    elif input_text.startswith("+") and input_text[1:].isdigit():
        target_id = await redis_client.get(f"phone:{input_text}")
        if not target_id:
            await message.reply(translations[lang]["user_not_found"], reply_markup=await get_main_menu(lang, user_id))
            return
        try:
            user_info = await bot.get_chat(target_id)
            target_name = user_info.username or user_info.first_name or target_id
        except Exception as e:
            logger.error(f"Error fetching user info for phone {input_text}: {e}")
            await message.reply(translations[lang]["user_not_found"], reply_markup=await get_main_menu(lang, user_id))
            return
    else:
        await message.reply(translations[lang]["invalid_phone_format"], reply_markup=await get_main_menu(lang, user_id))
        return
    
    try:
        await bot.send_chat_action(target_id, "typing")
    except Exception as e:
        logger.error(f"Cannot send message to user {target_id}: {e}")
        await message.reply(translations[lang]["user_blocked_bot"], reply_markup=await get_main_menu(lang, user_id))
        return
    
    data = await state.get_data()
    purchase_type = data["purchase_type"]
    price = (PREMIUM_PRICES.get(purchase_type.split("_")[1]) if purchase_type.startswith("premium_")
             else STARS_PRICES.get(purchase_type.split("_")[1]))
    await state.update_data(target_id=target_id, target_name=target_name)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=translations[lang]["confirm"], callback_data="confirm_purchase")],
        [InlineKeyboardButton(text=translations[lang]["cancel"], callback_data="cancel_purchase")],
        [InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_purchase_type")]
    ])
    await message.reply(
        translations[lang]["confirm_purchase"].format(type=purchase_type, target=target_name, price=price),
        reply_markup=keyboard
    )
    await state.set_state(UserStates.PURCHASE_CONFIRM)

@router.callback_query(F.data == "cancel_purchase")
async def cancel_purchase(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = str(callback_query.from_user.id)
    lang = await get_user_language(user_id)
    await callback_query.message.delete()
    await callback_query.message.answer(translations[lang]["no_pending_action"], reply_markup=await get_main_menu(lang, user_id))
    await state.clear()

@router.message(StateFilter(UserStates.ENTER_PHONE_NUMBER), F.text == translations["fa"].get("cancel", "لغو"))
async def cancel_contact_share(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    lang = await get_user_language(user_id)
    await message.reply(translations[lang]["bank_card_canceled"], reply_markup=await get_main_menu(lang, user_id))
    await state.clear()



@router.callback_query(StateFilter(UserStates.PURCHASE_CONFIRM), F.data == "confirm_purchase")
async def confirm_purchase(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = str(callback_query.from_user.id)
    lang = await get_user_language(user_id)
    data = await state.get_data()
    purchase_type = data.get("purchase_type")
    target_id = data.get("target_id")
    target_name = data.get("target_name")
    plan_category = data.get("plan_category")
    price = (PREMIUM_PRICES.get(purchase_type.split("_")[1]) if purchase_type.startswith("premium_")
             else STARS_PRICES.get(purchase_type.split("_")[1]))

    # بررسی کارت تأییدشده در SQLite
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT expiry, photo_file_id FROM bank_cards WHERE user_id = ? AND status = "approved" ORDER BY timestamp DESC LIMIT 1', (user_id,))
    approved_card = cursor.fetchone()
    conn.close()

    is_bank_verified = False
    expiry_dt = None
    photo_file_id = None
    if approved_card and approved_card[0]:
        expiry_dt = datetime.fromisoformat(approved_card[0])
        photo_file_id = approved_card[1]
        is_bank_verified = datetime.now() <= expiry_dt

    if is_bank_verified:
        expiry_str = expiry_dt.strftime("%H:%M:%S") if expiry_dt else "23:59:59"
        if photo_file_id:
            try:
                await bot.send_photo(
                    user_id,
                    photo=photo_file_id,
                    caption=translations[lang]["confirmed_bank_card_info"]
                )
            except Exception as e:
                logger.error(f"Error sending bank card photo to {user_id}: {e}")
                await callback_query.message.reply(translations[lang]["error_sending_photo"])

        try:
            await callback_query.message.edit_text(
                translations[lang]["payment_instructions"].format(
                    card_number=BANK_CARD_NUMBER, amount=price
                ) + f"\n{translations[lang]['expiry_info'].format(expiry=expiry_str)}"
            )
        except Exception as e:
            logger.error(f"Error editing message for user {user_id}: {e}")
            await callback_query.message.reply(
                translations[lang]["payment_instructions"].format(
                    card_number=BANK_CARD_NUMBER, amount=price
                ) + f"\n{translations[lang]['expiry_info'].format(expiry=expiry_str)}"
            )

        await callback_query.message.reply(translations[lang]["send_receipt_photo"])
        pending_data = {
            "purchase_type": purchase_type,
            "target_id": target_id,
            "target_name": target_name,
            "plan_category": plan_category,
            "price": price
        }
        await redis_client.set(f"user:{user_id}:pending_purchase", json.dumps(pending_data), ex=3600)
        await state.set_state(UserStates.VERIFY_RECEIPT)
        await state.update_data(purchase_type=purchase_type, target_id=target_id, target_name=target_name, plan_category=plan_category, price=price)
    else:
        pending_data = {
            "purchase_type": purchase_type,
            "target_id": target_id,
            "target_name": target_name,
            "plan_category": plan_category,
            "price": price
        }
        await redis_client.set(f"user:{user_id}:pending_purchase", json.dumps(pending_data), ex=3600)
        await callback_query.message.edit_text(translations[lang]["bank_card_required"])
        share_keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📞", request_contact=True)],
                [KeyboardButton(text=translations[lang]["cancel"])]
            ],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await callback_query.message.reply(
            translations[lang]["enter_phone_number"],
            reply_markup=share_keyboard
        )
        await state.set_state(UserStates.ENTER_PHONE_NUMBER)
        await state.update_data(purchase_type=purchase_type, target_id=target_id, target_name=target_name, plan_category=plan_category, price=price)

async def retry_purchase_type(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = str(callback_query.from_user.id)
    lang = await get_user_language(user_id)
    plan_category = "premium" if callback_query.data == "retry_premium" else "stars"
    await state.update_data(plan_category=plan_category)
    if plan_category == "premium":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"{plan} - {price:,} IRR", callback_data=f"premium_{plan}")]
            for plan, price in PREMIUM_PRICES.items()
        ] + [[InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_main")]])
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"{plan} - {price:,} IRR", callback_data=f"stars_{plan}")]
            for plan, price in STARS_PRICES.items()
        ] + [[InlineKeyboardButton(text=translations[lang]["back"], callback_data="back_to_main")]])
    await callback_query.message.edit_text(translations[lang]["select_plan"], reply_markup=keyboard)
    await state.set_state(UserStates.PURCHASE_TYPE)

async def main():
    # پاک‌سازی داده‌های کارت‌های بانکی در شروع
    await load_prices()
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        logger.info("Bot session closed")

if __name__ == '__main__':
    asyncio.run(main())
