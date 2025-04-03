import datetime
import logging
import os
from dotenv import load_dotenv


import schedule
import time
import asyncio
import traceback
from telegram import Bot
from telegram.ext import Application
from mpmath import mp
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger


mp.dps = 61
deerday_chance = 12

load_dotenv
# Вставьте сюда ваш токен бота и ID канала
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHANNEL_ID = os.getenv('TELEGRAM_CHANNEL_ID')
ADMIN_ID = os.getenv('8152099368')

# Настроим логирование для отладки
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Создадим объект бота
bot = Bot(token=BOT_TOKEN)








# Функция для отправки уведомлений об ошибках
async def notify_admin(error_message):
    try:
        await bot.send_message(chat_id=ADMIN_ID, text=error_message)
        logger.info(f"Admin notified: {error_message}")
    except Exception as e:
        logger.error(f"Failed to notify admin: {e}")


# Асинхронная функция для отправки сообщений
async def send_message(text):
    try:
        await bot.send_message(chat_id=CHANNEL_ID, text=text)
        logger.info(f"Message sent: {text}")
    except Exception as e:
        logger.error(f"Error sending message: {e}")

# Асинхронная функция для отправки фото
async def send_photo(photo_path):
    try:
        with open(photo_path, 'rb') as photo_file:
            await bot.send_photo(chat_id=CHANNEL_ID, photo=photo_file)
        logger.info("Photo sent successfully.")
    except Exception as e:
        logger.error(f"Error sending photo: {e}")

# Основная асинхронная функция для выполнения задачи
async def post_photo():

    time = datetime.now()
    day, month, year = time.day, time.month, time.year
    pi_modifier = int(str(mp.pi)[14:][day])
    sum = day + month + year + pi_modifier
    rand_num = sum % deerday_chance

    if rand_num== min(5, deerday_chance-1):
        print("Лосятница")
        photo_path = 'deerday.jpg'
    else:
        print("Свинятница")
        photo_path = 'image.jpg'

    logger.info("Starting to post photo.")
    #await send_message("Бот запустился, проверил: Отправляю пост с фотографией.")
    await send_photo(photo_path)  # Отправляем фотографию в канал

# Асинхронная функция для периодического выполнения задачи
async def periodic_task(interval, task, *args):
    while True:
        await task(*args)
        await asyncio.sleep(interval)

# Главная асинхронная функция
async def main():

    scheduler = AsyncIOScheduler()
    
    # Добавляем задачу в расписание: каждую пятницу в 10:00
    scheduler.add_job(post_photo, CronTrigger(day_of_week='fri', hour=10, minute=00))
    # Запускаем планировщик
    scheduler.start()
    
    logger.info("Планировщик запущен. Ожидание задач...")

    try:
        while True:
            await asyncio.sleep(3600)  # Спим по часу
    except asyncio.CancelledError as e:
            error_trace = traceback.format_exc()
            error_message = f"Произошла ошибка: {str(e)}\n\nТрассировка:\n{error_trace}"
            notify_admin(error_message)
            pass
    finally:
            scheduler.shutdown()


    # Запускаем периодическую задачу с интервалом 15 минут (900 секунд)
    # asyncio.create_task(periodic_task(10, post_photo))

    # while True:
    #     await asyncio.sleep(3600)  

# Запуск асинхронного цикла
if __name__ == '__main__':
    asyncio.run(main())