import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from aiogram import Bot
from src.pipeline.digest_builder import DigestBuilder
from src.config import SchedulerConfig

logger = logging.getLogger(__name__)

class DigestScheduler:
    def __init__(self, scheduler_config: SchedulerConfig, builder: DigestBuilder, bot: Bot, admin_id: int):
        self.config = scheduler_config
        self.builder = builder
        self.bot = bot
        self.admin_id = admin_id
        self.scheduler = AsyncIOScheduler()

    async def _send_morning_digest(self):
        if not self.admin_id:
            logger.warning("Admin ID не настроен. Дайджест некому отправить.")
            return

        logger.info("Запуск планового утреннего дайджеста (08:50)...")
        try:
            res = await self.builder.generate_digest()
            text = f"☀️ *Доброе утро! Ваша сводка на 08:50:*\n\n" + res["text"]
            if len(text) > 4000:
                for x in range(0, len(text), 4000):
                    await self.bot.send_message(self.admin_id, text[x:x+4000], parse_mode="Markdown")
            else:
                await self.bot.send_message(self.admin_id, text, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Ошибка при отправке утреннего дайджеста: {e}")

    def start(self):
        # Парсим крон 50 8 * * * (08:50 ежедневно)
        trigger = CronTrigger.from_crontab(self.config.digest_cron)
        self.scheduler.add_job(self._send_morning_digest, trigger=trigger, id="morning_digest")
        self.scheduler.start()
        logger.info(f"Планировщик запущен. Расписание дайджеста: {self.config.digest_cron}")

    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown()
