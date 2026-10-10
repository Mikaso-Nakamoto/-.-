import os
import re
import html
import logging
import httpx
from pathlib import Path
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from aiogram import Bot
from aiogram.types import FSInputFile, LinkPreviewOptions, InputMediaPhoto, BufferedInputFile
from aiogram.exceptions import TelegramBadRequest

from src.pipeline.digest_builder import DigestBuilder
from src.config import SchedulerConfig, load_preferences
from src.bot.keyboards import get_digest_feedback_keyboard

logger = logging.getLogger(__name__)

async def download_image_as_input_file(url: str, timeout: float = 8.0) -> BufferedInputFile | None:
    if not url or not url.startswith("http"):
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
            })
            if resp.status_code == 200 and len(resp.content) > 400:
                ct = resp.headers.get("content-type", "").lower()
                ext = ".jpg"
                if "png" in ct:
                    ext = ".png"
                elif "webp" in ct:
                    ext = ".webp"
                return BufferedInputFile(resp.content, filename=f"image{ext}")
    except Exception as e:
        logger.warning(f"Не удалось скачать изображение по URL {url}: {e}")
    return None

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

            # 1. Отправляем фото и структурированный пост (в формате единой фото-карточки или с фото сверху)
            photo_file = None
            if lead_img and lead_img.startswith("http"):
                photo_file = await download_image_as_input_file(lead_img)

            sent_as_photo = False
            if photo_file and len(full_post) <= 1024:
                try:
                    await self.bot.send_photo(
                        target_admin,
                        photo=photo_file,
                        caption=full_post,
                        parse_mode="HTML",
                        reply_markup=kb
                    )
                    sent_as_photo = True
                except Exception as e:
                    logger.warning(f"Не удалось отправить фото-карточку ({e}), отправляем обычным сообщением...")

            if not sent_as_photo:
                # Если текст длиннее 1024 символов:
                # Сначала отправляем реальное фото источников наверх
                if photo_file:
                    try:
                        await self.bot.send_photo(target_admin, photo=photo_file)
                    except Exception:
                        pass
                elif lead_img and lead_img.startswith("http"):
                    try:
                        await self.bot.send_photo(target_admin, photo=lead_img)
                    except Exception:
                        pass

                # Затем отправляем текстовый пост
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
                            reply_markup=kb
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

    async def check_ross_name_youtube(self):
        """Проверяет канал @ross_name на появление новых видео на YouTube и отправляет мгновенный алерт"""
        target_admin = self.admin_id
        if not target_admin:
            saved_id = self.builder.storage.get_setting("admin_id")
            if saved_id and saved_id.isdigit():
                target_admin = int(saved_id)

        if not target_admin:
            return

        try:
            from src.collectors.telegram_collector import TelegramWebCollector
            collector = TelegramWebCollector([{"username": "ross_name", "category": "Военно-политическая аналитика & СВО"}])
            items = await collector.fetch_all(limit_per_channel=8)

            for it in items:
                content = it.get("content", "")
                yt_links = re.findall(r'(https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)[a-zA-Z0-9_-]+)', content)
                for link in yt_links:
                    if not self.builder.storage.is_youtube_video_seen(link):
                        logger.info(f"Обнаружено новое YouTube-видео у RossName: {link}")
                        self.builder.storage.mark_youtube_video_seen(link, it.get("title", ""))

                        title = it.get("title", "Новое видео на YouTube")
                        clean_text = re.sub(r'https?://\S+', '', content).strip()
                        alert_html = (
                            f"🎬 <b>НОВОЕ ВИДЕО: RossName на YouTube!</b>\n\n"
                            f"📌 <b>{html.escape(title[:120])}</b>\n\n"
                            f"{html.escape(clean_text[:400])}\n\n"
                            f"🔗 <a href=\"{link}\">Смотреть видео на YouTube</a>"
                        )

                        photo_file = None
                        if it.get("image_url"):
                            photo_file = await download_image_as_input_file(it["image_url"])

                        if photo_file and len(alert_html) <= 1024:
                            await self.bot.send_photo(target_admin, photo=photo_file, caption=alert_html, parse_mode="HTML")
                        else:
                            if photo_file:
                                try:
                                    await self.bot.send_photo(target_admin, photo=photo_file)
                                except Exception:
                                    pass
                            await self.bot.send_message(target_admin, alert_html, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Ошибка при проверке YouTube-видео у RossName: {e}")

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

        # Фоновый мониторинг ночных и утренних релизов видео RossName (каждые 10 минут)
        self.scheduler.add_job(
            self.check_ross_name_youtube,
            'interval',
            minutes=10,
            id='ross_name_youtube_tracker'
        )

        self.scheduler.start()
        logger.info(f"Планировщик запущен. Расписание дайджеста: {self.config.digest_cron} (Часовой пояс: {self.timezone_name})")

    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown()
