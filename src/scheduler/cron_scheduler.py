import os
import logging
from pathlib import Path
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from aiogram import Bot
from aiogram.types import FSInputFile, LinkPreviewOptions
from aiogram.exceptions import TelegramBadRequest

from src.pipeline.digest_builder import DigestBuilder
from src.config import SchedulerConfig, load_preferences
from src.bot.keyboards import get_digest_feedback_keyboard

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
        target_admin = self.admin_id
        if not target_admin:
            saved_id = self.builder.storage.get_setting("admin_id")
            if saved_id and saved_id.isdigit():
                target_admin = int(saved_id)

        if not target_admin:
            logger.warning("Admin ID не настроен. Дайджест некому отправить.")
            return

        logger.info(f"Запуск планового утреннего дайджеста (08:50, {self.timezone_name}) для ID={target_admin}...")
        try:
            res = await self.builder.generate_digest()
            if not res.get("success"):
                await self.bot.send_message(target_admin, f"⚠️ Не удалось сформировать утреннюю сводку: {res.get('text')}")
                return

            post_html = res.get("telegram_post_html") or res.get("text")
            lead_img = res.get("lead_image_url")
            html_path = res.get("full_report_html_path")
            md_path = res.get("full_report_md_path")
            md_file_id = Path(md_path).name if md_path else None
            web_app_url = os.getenv("WEB_APP_URL") or "http://192.168.0.169:8000"

            kb = get_digest_feedback_keyboard(web_app_url=web_app_url, md_file_id=md_file_id)

            greeting = f"☀️ <b>Доброе утро! Ваша аналитическая сводка на 08:50 ({self.timezone_name}):</b>\n\n"
            full_post = greeting + post_html

            # Настройки предпросмотра фото новости
            preview_options = None
            if lead_img and lead_img.startswith("http"):
                preview_options = LinkPreviewOptions(
                    url=lead_img,
                    prefer_large_media=True,
                    show_above_text=True
                )

            # 1. Отправляем визуальный пост с нативными сворачиваемыми блоками
            try:
                if len(full_post) > 4000:
                    for x in range(0, len(full_post), 4000):
                        await self.bot.send_message(target_admin, full_post[x:x+4000], parse_mode="HTML")
                    await self.bot.send_message(target_admin, "💬 <b>Действия со сводкой:</b>", parse_mode="HTML", reply_markup=kb)
                else:
                    await self.bot.send_message(
                        target_admin,
                        full_post,
                        parse_mode="HTML",
                        reply_markup=kb,
                        link_preview_options=preview_options
                    )
            except TelegramBadRequest as e:
                logger.warning(f"Telegram parse error in morning digest ({e}), sending plain text...")
                await self.bot.send_message(target_admin, full_post, reply_markup=kb)

            # 2. Отправляем полноценный .MD документ прямо в Telegram
            if md_path and os.path.exists(md_path):
                today_str = datetime.now().strftime("%d.%m.%Y")
                doc = FSInputFile(md_path, filename=f"Digest_{today_str}.md")
                await self.bot.send_document(
                    target_admin,
                    document=doc,
                    caption="📑 <b>Полный аналитический отчет со всеми таблицами</b>\n<i>Нажмите на файл — откроется прямо внутри Telegram.</i>",
                    parse_mode="HTML"
                )

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
