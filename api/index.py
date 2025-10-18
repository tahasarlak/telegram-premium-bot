import os
from flask import Flask, request, jsonify
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from translations import translations
from dotenv import load_dotenv
import asyncio
import logging
import redis.asyncio as redis
import sqlite3
from datetime import datetime
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

load_dotenv()
app = Flask(__name__)

# تنظیمات logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# تنظیمات بات
BOT_TOKEN = os.getenv("BOT_TOKEN")
REDIS_HOST = os.getenv("REDIS_HOST")
REDIS_PORT = int(os.getenv("REDIS_PORT", 13206))
REDIS_USERNAME = os.getenv("REDIS_USERNAME", "default")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")
DB_FILE = "bot_database.db"

# Redis Client
redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    username=REDIS_USERNAME,
    password=REDIS_PASSWORD,
    decode_responses=True
)
logger.info("Redis client initialized")

# Aiogram Bot و Dispatcher
bot = Bot(token=BOT_TOKEN)
storage = RedisStorage(redis=redis_client)
dp = Dispatcher(storage=storage)

# States
class UserStates(StatesGroup):
    SELECT_LANGUAGE = State()

# Handler ساده برای /start
@dp.message(Command("start"))
async def start_command(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    lang = await get_user_language(user_id)
    user_name = message.from_user.username or message.from_user.first_name or user_id
    await message.reply(translations[lang]["welcome"].format(name=user_name))
    logger.info(f"User {user_id} triggered /start")

# تابع برای زبان کاربر
async def get_user_language(user_id):
    lang = await redis_client.get(f"user:{user_id}:language")
    if not lang:
        lang = "fa"
        await redis_client.set(f"user:{user_id}:language", lang)
    return lang

# Webhook endpoint
@app.route("/", methods=["POST"])
async def webhook():
    update = types.Update(**request.get_json())
    await dp.feed_update(bot, update)
    return jsonify({"status": "ok"})

# برای تست لوکال
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))