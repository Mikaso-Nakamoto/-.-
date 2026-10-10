import os
import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from aiogram import Bot
from src.pipeline.digest_builder import DigestBuilder
from src.config import SchedulerConfig, load_preferences

logger = logging.getLogger(__name__)

class DigestScheduler:
    def __init__(self, scheduler_config: SchedulerConfig, builder: DigestBuilder, bot: Bot, admin_id: int):
        self.config = scheduler_config
        self.builder = builder
        self.bot = bot
        self.admin_id = admin_id
        self.scheduler = AsyncIOScheduler()
        prefs = load_preferences()
        self.timezone_name = os.getenv("TZ") or prefs.get("user", {}).get("timezone", "Europe/Moscow")

    async def _send_morning_digest(self):
        if not self.admin_id:
            logger.warning("Admin ID не настроен. Дайджест некому отправить.")
            return

        logger.info(f"Запуск планового утреннего дайджеста (08:50, {self.timezone_name})...")
        try:
            res = await self.builder.generate_digest()
            text = f"☀️ *Доброе утро! Ваша сводка на 08:50 ({self.timezone_name}):*\n\n" + res["text"]
            if len(text) > 4000:
                for x in range(0, len(text), 4000):
                    await self.bot.send_message(self.admin_id, text[x:x+4000], parse_mode="Markdown")
            else:
                await self.bot.send_message(self.admin_id, text, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Ошибка при отправке утреннего дайджеста: {e}")

    def get_next_run_time(self) -> str:
        job = self.scheduler.get_job("morning_digest")
        if job and job.next_run_time:
            return job.next_run_time.strftime("%d.%m.%Y в %H:%M")
        return f"Ежедневно в 08:50 ({self.timezone_name})"

    def start(self):
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(self.timezone_name)
        except Exception:
            tz = self.timezone_name

        trigger = CronTrigger.from_crontab(self.config.digest_cron, timezone=tz)
        self.scheduler.add_job(self._send_morning_digest, trigger=trigger, id="morning_digest")
        self.scheduler.start()
        logger.info(f"Планировщик запущен. Расписание дайджеста: {self.config.digest_cron} (Часовой пояс: {self.timezone_name})")

    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown()
