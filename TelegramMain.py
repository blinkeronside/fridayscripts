"""Friday Telegram publisher. Use --check for a completely offline preflight."""

import argparse
import asyncio
import logging
import os
import re
import signal
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.events import EVENT_JOB_MAX_INSTANCES, EVENT_JOB_MISSED
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
from mpmath import mp
from telegram import Bot
from telegram.error import BadRequest, NetworkError, TelegramError
from telegram.request import HTTPXRequest

BASE_DIR = Path(__file__).resolve().parent
PHOTO_NAMES = ("deerday.jpg", "newfriday.jpg")
DEFAULT_TIMEZONE = "Asia/Yekaterinburg"
PHOTO_TIMEOUT = 45
NOTIFY_TIMEOUT = 15
SHUTDOWN_TIMEOUT = 90
MISFIRE_GRACE_TIME = 300
deerday_chance = 12
mp.dps = 61
logger = logging.getLogger(__name__)


class ConfigError(ValueError):
    """Safe diagnostic containing variable/file names, never their values."""


class SecretFormatter(logging.Formatter):
    def __init__(self, token=""):
        super().__init__("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
        self.token = token

    def format(self, record):
        # Sanitize the final output, including third-party exception tracebacks.
        output = super().format(record)
        if self.token:
            output = output.replace(self.token, "[REDACTED]")
        return re.sub(r"\b\d{5,}:[A-Za-z0-9_-]{20,}", "[REDACTED]", output)


def configure_logging(token=""):
    handler = logging.StreamHandler()
    handler.setFormatter(SecretFormatter(token))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    for name in ("httpx", "httpcore", "telegram"):
        logging.getLogger(name).setLevel(logging.WARNING)


@dataclass(frozen=True)
class Config:
    token: str = field(repr=False)
    channel_id: str = field(repr=False)
    admin_id: int = field(repr=False)
    timezone: ZoneInfo


def load_config(*, use_dotenv=True):
    if use_dotenv:
        # Explicit path; process environment (including systemd) takes precedence.
        load_dotenv(BASE_DIR / ".env", override=False, interpolate=False)
    required = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID", "ADMIN_ID")
    values = {name: os.environ.get(name, "").strip() for name in required}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ConfigError("Не заданы обязательные переменные: " + ", ".join(missing))
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", values["TELEGRAM_BOT_TOKEN"]):
        raise ConfigError("Неверный формат TELEGRAM_BOT_TOKEN")
    channel = values["TELEGRAM_CHANNEL_ID"]
    if not (re.fullmatch(r"-[1-9]\d*", channel)
            or re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{4,31}", channel)):
        raise ConfigError("TELEGRAM_CHANNEL_ID: нужен отрицательный ID или @username")
    if not re.fullmatch(r"[1-9]\d*", values["ADMIN_ID"]):
        raise ConfigError("ADMIN_ID: нужен положительный числовой ID пользователя")
    timezone_name = os.environ.get("FRIDAY_TIMEZONE", DEFAULT_TIMEZONE).strip()
    try:
        timezone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError("FRIDAY_TIMEZONE: нужен доступный часовой пояс IANA") from None
    return Config(values["TELEGRAM_BOT_TOKEN"], channel, int(values["ADMIN_ID"]), timezone)


def validate_photos(base_dir=BASE_DIR):
    for name in PHOTO_NAMES:
        path = base_dir / name
        try:
            if not path.is_file() or path.stat().st_size == 0:
                raise OSError("missing or empty file")
            with path.open("rb") as photo:
                if not photo.read(1):
                    raise OSError("empty file")
        except OSError:
            raise ConfigError(f"Фотография отсутствует, пуста или недоступна для чтения: {name}") from None


def friday_trigger(timezone):
    return CronTrigger(day_of_week="fri", hour=10, minute=00, timezone=timezone)


def select_photo(now):
    # Keep the original pi digit, date arithmetic and 1/12 selection rule.
    day, month, year = now.day, now.month, now.year
    pi_modifier = int(str(mp.pi)[14:][day])
    rand_num = (day + month + year + pi_modifier) % deerday_chance
    name = "deerday.jpg" if rand_num == min(5, deerday_chance - 1) else "newfriday.jpg"
    return BASE_DIR / name


async def send_message(bot, chat_id, text):
    # Intentionally propagate API failures to the caller. No automatic retries.
    return await asyncio.wait_for(
        bot.send_message(chat_id=chat_id, text=text), timeout=NOTIFY_TIMEOUT
    )


async def send_photo(bot, chat_id, photo_path):
    with photo_path.open("rb") as photo:
        if not photo.read(1):
            raise OSError("empty photo")
        photo.seek(0)
        return await asyncio.wait_for(
            bot.send_photo(chat_id=chat_id, photo=photo), timeout=PHOTO_TIMEOUT
        )


async def notify_admin(bot, config, text, *, context):
    try:
        await send_message(bot, config.admin_id, text)
        logger.info("Уведомление администратору доставлено: %s", context)
        return True
    except Exception as error:
        # Never log exception text/URL, nor send a traceback to Telegram.
        logger.error("Не удалось уведомить администратора (%s): %s; повторов нет",
                     context, type(error).__name__)
        return False


async def post_photo(bot, config):
    try:
        photo_path = select_photo(datetime.now(config.timezone))
        logger.info("Публикация фотографии: %s", photo_path.name)
        result = await send_photo(bot, config.channel_id, photo_path)
    except Exception as error:
        if isinstance(error, BadRequest):
            status = "Telegram отклонил публикацию. Исправьте причину перед новой попыткой."
        elif isinstance(error, (NetworkError, TimeoutError)):
            status = "Результат публикации неизвестен: Telegram мог принять пост. Проверьте канал."
        elif isinstance(error, TelegramError):
            status = "Telegram отклонил публикацию. Исправьте причину перед новой попыткой."
        else:
            status = "Публикация не выполнена из-за локальной ошибки."
        text = f"{status} Тип ошибки: {type(error).__name__}. Автоматического повтора нет."
        logger.error("%s", text)
        await notify_admin(bot, config, text, context="ошибка публикации")
        raise
    # Notification failure is separate from the photo transaction and never retries it.
    logger.info("Telegram подтвердил публикацию фотографии")
    await notify_admin(bot, config, "Telegram подтвердил публикацию пятничной фотографии.",
                       context="успех публикации")
    return result


class Publisher:
    def __init__(self, bot, config):
        self.bot = bot
        self.config = config
        self.active_tasks = set()
        self.stopping = False

    async def run_job(self):
        # A queued job must not start sending after shutdown begins.
        if self.stopping:
            return
        task = asyncio.current_task()
        self.active_tasks.add(task)
        try:
            await post_photo(self.bot, self.config)
        except asyncio.CancelledError:
            logger.warning("Отправка отменена при остановке; результат может быть неизвестен. Повтора нет.")
            # Do not turn an intentional cancellation into an APScheduler crash alert.
        except Exception as error:
            # Publication already reported the failure. Contain it at the job boundary
            # so APScheduler cannot emit an unsanitized exception or retry the photo.
            logger.error("Задание завершилось с ошибкой: %s", type(error).__name__)
        finally:
            self.active_tasks.discard(task)

    async def stop(self, scheduler):
        self.stopping = True
        if scheduler.running:
            scheduler.pause()
        # Let already queued coroutines observe stopping before taking the snapshot.
        await asyncio.sleep(0)
        tasks = set(self.active_tasks)
        if tasks:
            logger.info("Ожидание текущей отправки перед остановкой (до %s секунд)", SHUTDOWN_TIMEOUT)
            _, pending = await asyncio.wait(tasks, timeout=SHUTDOWN_TIMEOUT)
            if pending:
                logger.warning("Время остановки истекло; результат отправки может быть неизвестен. Повтора нет.")
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
        if scheduler.running:
            # APScheduler 3.11.0 cancels pending async jobs even with wait=True.
            # Drain them ourselves first, then wait for the shutdown callback.
            scheduler.shutdown(wait=False)
            await asyncio.sleep(0)


def scheduler_event(event):
    if event.code == EVENT_JOB_MISSED:
        logger.warning("Публикация пропущена: задержка превысила %s секунд; автоматического повтора нет",
                       MISFIRE_GRACE_TIME)
    else:
        logger.warning("Публикация пропущена: предыдущее задание ещё выполняется")


def create_scheduler(publisher, config):
    scheduler = AsyncIOScheduler(timezone=config.timezone)
    scheduler.add_job(publisher.run_job, friday_trigger(config.timezone), id="friday_photo",
                      max_instances=1, coalesce=True, misfire_grace_time=MISFIRE_GRACE_TIME)
    scheduler.add_listener(scheduler_event, EVENT_JOB_MISSED | EVENT_JOB_MAX_INSTANCES)
    return scheduler


def install_signal_handlers(stop_event):
    loop = asyncio.get_running_loop()
    old_handlers = {}
    native_signals = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        old_handlers[sig] = signal.getsignal(sig)
        try:
            loop.add_signal_handler(sig, stop_event.set)
            native_signals.append(sig)
        except NotImplementedError:  # Windows development/test environment
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop_event.set))

    def restore():
        for sig in native_signals:
            loop.remove_signal_handler(sig)
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
    return restore


async def main(config, stop_event=None):
    stop_event = stop_event if stop_event is not None else asyncio.Event()
    restore_signals = install_signal_handlers(stop_event)
    try:
        async with AsyncExitStack() as requests:
            request = HTTPXRequest(connection_pool_size=2, connect_timeout=10, read_timeout=15,
                                   write_timeout=15, media_write_timeout=20, pool_timeout=5)
            requests.push_async_callback(request.shutdown)
            updates_request = HTTPXRequest()
            requests.push_async_callback(updates_request.shutdown)
            # PTB 22.0 Bot.shutdown() returns early if initialize() failed.
            # Explicit, idempotent request cleanup covers that startup failure too.
            async with Bot(token=config.token, request=request,
                           get_updates_request=updates_request) as bot:
                if stop_event.is_set():
                    return
                publisher = Publisher(bot, config)
                scheduler = create_scheduler(publisher, config)
                try:
                    scheduler.start()
                    next_run = scheduler.get_job("friday_photo").next_run_time
                    startup_text = ("Планировщик запущен. Следующая публикация: "
                                    f"{next_run.isoformat()} ({config.timezone.key}).")
                    logger.info("%s", startup_text)
                    await notify_admin(bot, config, startup_text, context="запуск планировщика")
                    await stop_event.wait()
                finally:
                    await publisher.stop(scheduler)
        logger.info("Планировщик и Bot остановлены")
    except Exception:
        if stop_event.is_set():
            logger.info("Остановка во время запуска или закрытия Bot")
        else:
            raise
    finally:
        restore_signals()


def cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Проверить настройки и фото без Telegram API")
    parser.add_argument("--no-dotenv", action="store_true", help="Использовать только окружение процесса")
    args = parser.parse_args(argv)
    configure_logging()
    try:
        config = load_config(use_dotenv=not args.no_dotenv)
        configure_logging(config.token)
        validate_photos()
        if args.check:
            now = datetime.now(config.timezone)
            next_run = friday_trigger(config.timezone).get_next_fire_time(None, now)
            logger.info("Конфигурация и обе фотографии проверены. Следующая публикация: %s (%s)",
                        next_run.isoformat(), config.timezone.key)
        else:
            asyncio.run(main(config))
        return 0
    except ConfigError as error:
        logger.error("%s", error)
    except KeyboardInterrupt:
        logger.info("Остановка по SIGINT")
        return 0
    except Exception as error:
        logger.error("Фатальная ошибка запуска/остановки: %s", type(error).__name__)
    return 1


if __name__ == "__main__":
    raise SystemExit(cli())
