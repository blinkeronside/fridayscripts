"""Offline regression tests. All Telegram methods are mocked; no real .env is read."""

import asyncio
import io
import logging
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from telegram.error import BadRequest, Forbidden, TimedOut

import TelegramMain as app

FAKE_TOKEN = "123456789:" + "x" * 35
ENV = {
    "TELEGRAM_BOT_TOKEN": FAKE_TOKEN,
    "TELEGRAM_CHANNEL_ID": "-1001234567890",
    "ADMIN_ID": "123456789",
    "FRIDAY_TIMEZONE": "Asia/Yekaterinburg",
}


def config():
    with patch.dict(os.environ, ENV, clear=True):
        return app.load_config(use_dotenv=False)


def fake_bot():
    return SimpleNamespace(send_photo=AsyncMock(return_value="photo-response"),
                           send_message=AsyncMock(return_value="message-response"))


class ConfigTests(unittest.TestCase):
    def test_missing_and_empty_required_variables(self):
        for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID", "ADMIN_ID"):
            for value in (None, " "):
                env = dict(ENV)
                if value is None:
                    del env[name]
                else:
                    env[name] = value
                with self.subTest(name=name, value=value), patch.dict(os.environ, env, clear=True):
                    with self.assertRaises(app.ConfigError) as caught:
                        app.load_config(use_dotenv=False)
                    self.assertIn(name, str(caught.exception))
                    self.assertNotIn(FAKE_TOKEN, str(caught.exception))

    def test_invalid_timezone(self):
        for value in ("Mars/Olympus", "", "../etc/passwd"):
            with self.subTest(value=value), patch.dict(os.environ, {**ENV, "FRIDAY_TIMEZONE": value}, clear=True):
                with self.assertRaisesRegex(app.ConfigError, "FRIDAY_TIMEZONE"):
                    app.load_config(use_dotenv=False)

    def test_default_timezone_and_valid_username(self):
        env = {**ENV, "TELEGRAM_CHANNEL_ID": "@example_channel"}
        del env["FRIDAY_TIMEZONE"]
        with patch.dict(os.environ, env, clear=True):
            result = app.load_config(use_dotenv=False)
        self.assertEqual(result.timezone.key, "Asia/Yekaterinburg")
        self.assertNotIn(FAKE_TOKEN, repr(result))

    def test_invalid_ids_and_token_do_not_echo_values(self):
        for name, value in (("TELEGRAM_BOT_TOKEN", "private-invalid-value"),
                            ("ADMIN_ID", "bad-private-admin"),
                            ("TELEGRAM_CHANNEL_ID", "bad-private-channel")):
            with self.subTest(name=name), patch.dict(os.environ, {**ENV, name: value}, clear=True):
                with self.assertRaises(app.ConfigError) as caught:
                    app.load_config(use_dotenv=False)
                self.assertNotIn(value, str(caught.exception))

    def test_environment_wins_over_dotenv_at_script_location(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / ".env").write_text("ADMIN_ID=999\nFRIDAY_TIMEZONE=UTC\n", encoding="utf-8")
            with patch.object(app, "BASE_DIR", base), patch.dict(os.environ, ENV, clear=True):
                self.assertEqual(app.load_config().admin_id, 123456789)
            env = dict(ENV)
            del env["FRIDAY_TIMEZONE"]
            with patch.object(app, "BASE_DIR", base), patch.dict(os.environ, env, clear=True):
                self.assertEqual(app.load_config().timezone.key, "UTC")

    def test_missing_empty_and_unreadable_photos(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with self.assertRaises(app.ConfigError):
                app.validate_photos(base)
            for name in app.PHOTO_NAMES:
                (base / name).write_bytes(b"")
            with self.assertRaises(app.ConfigError):
                app.validate_photos(base)
            for name in app.PHOTO_NAMES:
                (base / name).write_bytes(b"photo")
            with patch.object(Path, "open", side_effect=PermissionError):
                with self.assertRaises(app.ConfigError):
                    app.validate_photos(base)
            app.validate_photos(base)

    def test_offline_check_from_another_working_directory(self):
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                with patch.dict(os.environ, ENV, clear=True), patch.object(app, "Bot") as bot:
                    with self.assertLogs(app.logger, level="INFO") as logs:
                        self.assertEqual(app.cli(["--check", "--no-dotenv"]), 0)
                    bot.assert_not_called()
                    self.assertIn("19:10:00+05:00", "\n".join(logs.output))
                    self.assertTrue(app.select_photo(datetime(2026, 2, 6)).is_absolute())
            finally:
                os.chdir(previous)

    def test_next_friday_before_and_after_deadline(self):
        tz = ZoneInfo("Asia/Yekaterinburg")
        trigger = app.friday_trigger(tz)
        cases = (
            (datetime(2026, 10, 1, 12, tzinfo=tz), datetime(2026, 10, 2, 19, 10, tzinfo=tz)),
            (datetime(2026, 10, 2, 19, 9, tzinfo=tz), datetime(2026, 10, 2, 19, 10, tzinfo=tz)),
            (datetime(2026, 10, 2, 19, 11, tzinfo=tz), datetime(2026, 10, 9, 19, 10, tzinfo=tz)),
            (datetime(2026, 10, 1, 22, tzinfo=timezone.utc), datetime(2026, 10, 2, 19, 10, tzinfo=tz)),
        )
        for now, expected in cases:
            with self.subTest(now=now):
                self.assertEqual(trigger.get_next_fire_time(None, now), expected)

    def test_original_photo_selection_regression(self):
        cases = {
            (2026, 2, 6): "newfriday.jpg",
            (2026, 3, 27): "deerday.jpg",
            (2026, 7, 31): "newfriday.jpg",
            (2026, 10, 2): "newfriday.jpg",
            (2026, 10, 30): "deerday.jpg",
        }
        for date, expected in cases.items():
            with self.subTest(date=date):
                self.assertEqual(app.select_photo(datetime(*date)).name, expected)

    def test_real_cli_check_in_subprocess_without_dotenv(self):
        env = {name: os.environ[name] for name in ("SYSTEMROOT", "WINDIR", "PATH") if name in os.environ}
        env.update(ENV)
        env["PYTHONIOENCODING"] = "utf-8"
        with tempfile.TemporaryDirectory() as directory:
            command = [sys.executable, str(app.BASE_DIR / "TelegramMain.py"), "--check", "--no-dotenv"]
            result = subprocess.run(command, cwd=directory, env=env, capture_output=True,
                                    text=True, encoding="utf-8", timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("19:10:00+05:00", result.stderr)
            self.assertNotIn(FAKE_TOKEN, result.stderr)
            del env["ADMIN_ID"]
            result = subprocess.run(command, cwd=directory, env=env, capture_output=True,
                                    text=True, encoding="utf-8", timeout=15)
            self.assertEqual(result.returncode, 1)
            self.assertIn("ADMIN_ID", result.stderr)
            self.assertNotIn(FAKE_TOKEN, result.stderr)

    def test_redaction_covers_http_urls_tracebacks_and_preserves_schedule(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(app.SecretFormatter(FAKE_TOKEN))
        log = logging.getLogger("test-redaction")
        log.addHandler(handler)
        log.propagate = False
        try:
            try:
                raise RuntimeError("https://api.telegram.org/bot" + FAKE_TOKEN + "/sendPhoto")
            except RuntimeError:
                log.error("Next: 2026-10-02T19:10:00+05:00", exc_info=True)
            output = stream.getvalue()
            self.assertNotIn(FAKE_TOKEN, output)
            self.assertIn("[REDACTED]", output)
            self.assertIn("19:10:00+05:00", output)
        finally:
            log.removeHandler(handler)
            log.propagate = True


class PublicationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = config()
        self.bot = fake_bot()

    async def test_success_notifies_only_after_telegram_response(self):
        sequence = []

        async def photo(**kwargs):
            self.assertFalse(kwargs["photo"].closed)
            sequence.append("photo")
            return "confirmed"

        async def message(**kwargs):
            sequence.append("notification")

        self.bot.send_photo.side_effect = photo
        self.bot.send_message.side_effect = message
        self.assertEqual(await app.post_photo(self.bot, self.config), "confirmed")
        self.assertEqual(sequence, ["photo", "notification"])
        self.bot.send_photo.assert_awaited_once()
        self.assertEqual(self.bot.send_message.call_args.kwargs["chat_id"], self.config.admin_id)
        self.assertTrue(self.bot.send_photo.call_args.kwargs["photo"].closed)

    async def test_photo_error_reports_unknown_result_without_retry(self):
        error = TimedOut(FAKE_TOKEN)
        self.bot.send_photo.side_effect = error
        with self.assertLogs(app.logger, level="ERROR") as logs:
            with self.assertRaises(TimedOut) as caught:
                await app.post_photo(self.bot, self.config)
        self.assertIs(caught.exception, error)
        self.bot.send_photo.assert_awaited_once()
        self.bot.send_message.assert_awaited_once()
        text = self.bot.send_message.call_args.kwargs["text"]
        self.assertIn("неизвестен", text)
        self.assertNotIn("подтвердил", text)
        self.assertNotIn(FAKE_TOKEN, text + "\n".join(logs.output))

    async def test_explicit_rejection_is_distinct_from_network_timeout(self):
        self.bot.send_photo.side_effect = BadRequest("rejected")
        with self.assertRaises(BadRequest):
            await app.post_photo(self.bot, self.config)
        self.assertIn("отклонил", self.bot.send_message.call_args.kwargs["text"])
        self.bot.send_photo.assert_awaited_once()

    async def test_success_notification_error_never_republishes(self):
        self.bot.send_message.side_effect = Forbidden(FAKE_TOKEN)
        with self.assertLogs(app.logger, level="ERROR") as logs:
            self.assertEqual(await app.post_photo(self.bot, self.config), "photo-response")
        self.bot.send_photo.assert_awaited_once()
        self.bot.send_message.assert_awaited_once()
        self.assertIn("успех публикации", "\n".join(logs.output))
        self.assertNotIn(FAKE_TOKEN, "\n".join(logs.output))

    async def test_photo_and_error_notification_failures_are_logged(self):
        self.bot.send_photo.side_effect = TimedOut(FAKE_TOKEN)
        self.bot.send_message.side_effect = Forbidden(FAKE_TOKEN)
        with self.assertLogs(app.logger, level="ERROR") as logs:
            with self.assertRaises(TimedOut):
                await app.post_photo(self.bot, self.config)
        self.assertIn("ошибка публикации", "\n".join(logs.output))
        self.assertNotIn(FAKE_TOKEN, "\n".join(logs.output))
        self.bot.send_photo.assert_awaited_once()

    async def test_send_message_propagates_original_failure(self):
        error = Forbidden("rejected")
        self.bot.send_message.side_effect = error
        with self.assertRaises(Forbidden) as caught:
            await app.send_message(self.bot, self.config.channel_id, "test")
        self.assertIs(caught.exception, error)

    async def test_date_selection_uses_configured_timezone(self):
        # UTC is still Thursday here, while the configured date is already Friday.
        local_now = datetime(2026, 10, 2, 1, tzinfo=self.config.timezone)
        with patch.object(app, "datetime") as clock, patch.object(app, "select_photo", wraps=app.select_photo) as select:
            clock.now.return_value = local_now
            await app.post_photo(self.bot, self.config)
            clock.now.assert_called_once_with(self.config.timezone)
            select.assert_called_once_with(local_now)

    async def test_job_failure_is_contained_at_scheduler_boundary(self):
        self.bot.send_photo.side_effect = TimedOut(FAKE_TOKEN)
        publisher = app.Publisher(self.bot, self.config)
        with self.assertLogs(app.logger, level="ERROR"):
            await publisher.run_job()
        self.assertFalse(publisher.active_tasks)
        self.bot.send_photo.assert_awaited_once()

    async def test_real_scheduler_drains_inflight_send_before_shutdown(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def photo(**kwargs):
            entered.set()
            await release.wait()
            return "confirmed"

        self.bot.send_photo.side_effect = photo
        publisher = app.Publisher(self.bot, self.config)
        scheduler = app.create_scheduler(publisher, self.config)
        job = scheduler.get_job("friday_photo")
        self.assertEqual(job.max_instances, 1)
        self.assertTrue(job.coalesce)
        self.assertEqual(job.misfire_grace_time, 300)
        scheduler.reschedule_job(job.id, trigger="date", run_date=datetime.now(self.config.timezone))
        scheduler.start()
        try:
            await asyncio.wait_for(entered.wait(), 2)
            stop = asyncio.create_task(publisher.stop(scheduler))
            await asyncio.sleep(0.02)
            self.assertFalse(stop.done())
            self.assertTrue(scheduler.running)
            release.set()
            await asyncio.wait_for(stop, 2)
            self.assertFalse(scheduler.running)
            await publisher.run_job()
            self.bot.send_photo.assert_awaited_once()
            self.bot.send_message.assert_awaited_once()
        finally:
            release.set()
            if scheduler.running:
                await publisher.stop(scheduler)

    async def test_forced_shutdown_cancels_without_crash_notification(self):
        entered = asyncio.Event()

        async def photo(**kwargs):
            entered.set()
            await asyncio.Event().wait()

        self.bot.send_photo.side_effect = photo
        publisher = app.Publisher(self.bot, self.config)
        scheduler = app.create_scheduler(publisher, self.config)
        scheduler.start()
        job = asyncio.create_task(publisher.run_job())
        await asyncio.wait_for(entered.wait(), 2)
        with patch.object(app, "SHUTDOWN_TIMEOUT", 0.01), self.assertLogs(app.logger, level="WARNING"):
            await publisher.stop(scheduler)
        await job
        self.bot.send_photo.assert_awaited_once()
        self.bot.send_message.assert_not_awaited()

    async def test_main_starts_scheduler_notifies_and_closes_bot(self):
        stop = asyncio.Event()
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=self.bot)
        context.__aexit__ = AsyncMock(return_value=False)

        async def startup_message(**kwargs):
            self.assertIn("Следующая публикация", kwargs["text"])
            self.assertIn("19:10:00+05:00", kwargs["text"])
            stop.set()

        self.bot.send_message.side_effect = startup_message
        restore = MagicMock()
        request = SimpleNamespace(shutdown=AsyncMock())
        with patch.object(app, "Bot", return_value=context), patch.object(app, "HTTPXRequest", return_value=request), \
                patch.object(app, "install_signal_handlers", return_value=restore):
            await app.main(self.config, stop)
        context.__aenter__.assert_awaited_once()
        context.__aexit__.assert_awaited_once()
        restore.assert_called_once()
        self.bot.send_photo.assert_not_awaited()
        self.bot.send_message.assert_awaited_once()

    async def test_real_bot_initialization_failure_closes_both_requests(self):
        # Exercise PTB 22.0's real initialize/__aenter__/shutdown with a mocked API.
        request = MagicMock(spec=app.HTTPXRequest)
        request.initialize = AsyncMock()
        request.shutdown = AsyncMock()
        request.post = AsyncMock(side_effect=TimedOut(FAKE_TOKEN))
        updates_request = MagicMock(spec=app.HTTPXRequest)
        updates_request.initialize = AsyncMock()
        updates_request.shutdown = AsyncMock()
        with patch.object(app, "HTTPXRequest", side_effect=[request, updates_request]), \
                patch.object(app, "install_signal_handlers", return_value=MagicMock()):
            with self.assertRaises(TimedOut):
                await app.main(self.config)
        request.post.assert_awaited_once()
        request.shutdown.assert_awaited_once()
        updates_request.shutdown.assert_awaited_once()

    async def test_signals_request_clean_stop_and_restore_handlers(self):
        for sig in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(signal=sig):
                stop = asyncio.Event()
                previous = signal.getsignal(sig)
                restore = app.install_signal_handlers(stop)
                try:
                    signal.raise_signal(sig)
                    await asyncio.wait_for(stop.wait(), 2)
                finally:
                    restore()
                self.assertEqual(signal.getsignal(sig), previous)


class ExitTests(unittest.TestCase):
    def test_invalid_configuration_exits_nonzero_offline(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(app, "Bot") as bot:
            self.assertEqual(app.cli(["--check", "--no-dotenv"]), 1)
            bot.assert_not_called()

    def test_fatal_startup_exits_nonzero_without_secret_traceback(self):
        with patch.dict(os.environ, ENV, clear=True), \
                patch.object(app, "main", AsyncMock(side_effect=RuntimeError(FAKE_TOKEN))):
            with self.assertLogs(app.logger, level="ERROR") as logs:
                self.assertEqual(app.cli(["--no-dotenv"]), 1)
            self.assertNotIn(FAKE_TOKEN, "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
