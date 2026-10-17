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
from aiogram.types import FSInputFile, LinkPreviewOptions, InputMediaPhoto, BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest

from src.pipeline.digest_builder import DigestBuilder
from src.config import SchedulerConfig, load_preferences, load_prompts
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

import asyncio

async def download_images_as_media_group(urls: list, max_count: int = 6) -> list:
    """Скачивает изображения из разных каналов и формирует альбом для утренней сводки"""
    if not urls:
        return []
    tasks = [download_image_as_input_file(u) for u in urls[:max_count]]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    media_items = []
    for r in results:
        if isinstance(r, BufferedInputFile):
            media_items.append(InputMediaPhoto(media=r))
    return media_items

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

            kb = get_digest_feedback_keyboard(md_file_id=md_file_id)

            greeting = f"☀️ <b>Доброе утро! Ваша аналитическая сводка на 08:50 ({self.timezone_name}):</b>\n\n"
            full_post = greeting + post_html

            # 1. Отправляем альбом (медиагруппу) из разных фото/видео источников одним сообщением-коллажем!
            source_images = res.get("source_images") or ([lead_img] if lead_img else [])
            media_group = await download_images_as_media_group(source_images, max_count=6)

            if len(media_group) >= 2:
                try:
                    await self.bot.send_media_group(target_admin, media=media_group)
                except Exception as e:
                    logger.warning(f"Не удалось отправить медиагруппу источников ({e}), пробуем одиночное фото...")
                    if media_group:
                        try:
                            await self.bot.send_photo(target_admin, photo=media_group[0].media)
                        except Exception:
                            pass
            elif len(media_group) == 1:
                try:
                    await self.bot.send_photo(target_admin, photo=media_group[0].media)
                except Exception as e:
                    logger.warning(f"Не удалось отправить фото источника: {e}")
            elif lead_img and lead_img.startswith("http"):
                try:
                    await self.bot.send_photo(target_admin, photo=lead_img)
                except Exception:
                    pass

            # 2. Затем отправляем структурированный текстовый пост
            from src.bot.handlers import split_telegram_html
            chunks = split_telegram_html(full_post, max_chunk=3800)

            for idx, chunk in enumerate(chunks):
                is_last = (idx == len(chunks) - 1)
                cur_markup = kb if is_last else None
                try:
                    await self.bot.send_message(
                        target_admin,
                        chunk,
                        parse_mode="HTML",
                        reply_markup=cur_markup
                    )
                except TelegramBadRequest as e:
                    logger.warning(f"Telegram parse error in morning digest chunk {idx} ({e}), sending sanitized plain text...")
                    clean_text = html.unescape(re.sub(r'<[^>]+>', '', chunk))
                    await self.bot.send_message(target_admin, clean_text, reply_markup=cur_markup)
                except Exception as e:
                    logger.error(f"Failed to send digest chunk {idx}: {e}")

            # 3. Отправляем полноценный .MD документ прямо в Telegram
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

                        # Очищаем текст от ссылок и пустых строк
                        text_no_url = re.sub(r'https?://\S+', '', content)
                        raw_lines = [line.strip() for line in text_no_url.splitlines()]
                        raw_lines = [line for line in raw_lines if line]

                        # Если смайлики/символы идут столбиком (короткие строки <= 4 симв.),
                        # склеиваем их в одну аккуратную строку через пробел вместо гигантской лесенки
                        compact_lines = []
                        short_buf = []
                        for line in raw_lines:
                            if len(line) <= 4:
                                short_buf.append(line)
                            else:
                                if short_buf:
                                    compact_lines.append(" ".join(short_buf))
                                    short_buf = []
                                compact_lines.append(line)
                        if short_buf:
                            compact_lines.append(" ".join(short_buf))

                        clean_desc = "\n".join(compact_lines).strip()

                        # Формируем компактное, аккуратное сообщение без дублирования блоков
                        alert_parts = ["🎬 <b>НОВОЕ ВИДЕО: RossName на YouTube!</b>"]
                        if clean_desc:
                            alert_parts.append(f"<blockquote>{html.escape(clean_desc[:400])}</blockquote>")
                        alert_parts.append(f"🔗 <a href=\"{link}\">Смотреть видео на YouTube</a>")

                        alert_html = "\n\n".join(alert_parts)
                        reply_kb = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🎬 Смотреть на YouTube", url=link)]
                        ])

                        photo_file = None
                        if it.get("image_url"):
                            photo_file = await download_image_as_input_file(it["image_url"])

                        if photo_file and len(alert_html) <= 1024:
                            await self.bot.send_photo(target_admin, photo=photo_file, caption=alert_html, parse_mode="HTML", reply_markup=reply_kb)
                        else:
                            if photo_file:
                                try:
                                    await self.bot.send_photo(target_admin, photo=photo_file)
                                except Exception:
                                    pass
                            await self.bot.send_message(target_admin, alert_html, parse_mode="HTML", reply_markup=reply_kb)
        except Exception as e:
            logger.error(f"Ошибка при проверке YouTube-видео у RossName: {e}")

    async def check_medvedev_channel(self):
        """
        Фоновый трекер канала @medvedev_telegram (Дмитрий Медведев).
        При появлении нового поста:
        1. Немедленно дублирует пост пользователю с пометкой '⚡️ Дмитрий Анатольевич вещает!' и ссылкой.
        2. Обрабатывает пост ИИ в первую очередь (главный посыл, адресат, угрозы/последствия).
        3. Фиксирует публикацию в БД во избежание дублей.
        """
        target_admin = self.admin_id
        if not target_admin:
            saved_id = self.builder.storage.get_setting("admin_id")
            if saved_id and saved_id.isdigit():
                target_admin = int(saved_id)

        if not target_admin:
            return

        try:
            from src.collectors.telegram_collector import TelegramWebCollector
            collector = TelegramWebCollector([{"username": "medvedev_telegram", "category": "Заявления & Геополитика"}])
            items = await collector.fetch_all(limit_per_channel=5)

            unseen_items = [
                it for it in items
                if it.get("url") and not self.builder.storage.is_medvedev_post_seen(it.get("url"))
            ]

            # Защита от спама при холодном старте с пустой базой:
            # если накопилось сразу много постов, маркируем старые в БД и шлем алерт только на самый свежий
            if len(unseen_items) > 2:
                for it in unseen_items[:-1]:
                    self.builder.storage.mark_medvedev_post_seen(it.get("url"))
                    self.builder.storage.save_news_items([it])
                items_to_alert = [unseen_items[-1]]
            else:
                items_to_alert = unseen_items

            for it in items_to_alert:
                url = it.get("url") or ""
                if not url:
                    continue

                logger.info(f"⚡️ Обнаружено новое заявление в канале @medvedev_telegram: {url}")
                self.builder.storage.mark_medvedev_post_seen(url)
                self.builder.storage.save_news_items([it])

                content = (it.get("content") or "").strip()
                title = it.get("title", "Заявление Дмитрия Медведева")

                # Экспресс-обработка поста через ИИ в первую очередь
                ai_analysis = ""
                try:
                    ai_prompt = (
                        f"Текст заявления Дмитрия Медведева:\n{content[:3000]}\n\n"
                        "Сделай краткий экспресс-анализ заявления в 2-3 предложениях на русском языке: "
                        "каков ключевой политический посыл, кому адресован сигнал и какие прямые последствия озвучены.\n\n"
                        "СТРОЖАЙШИЕ ПРАВИЛА:\n"
                        "1. Выдай ТОЛЬКО чистый текст анализа (2-3 предложения).\n"
                        "2. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО повторять или повторно цитировать текст заявления (оно уже выведено пользователю).\n"
                        "3. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО добавлять блоки цитат <blockquote>, подписи '— @канал', 'Источники: [@канал](ссылка)' или любые шаблоны.\n"
                        "4. Начинай сразу с анализа сути без лишних вводных слов."
                    )
                    prompts = load_prompts().get("system_prompts", {})
                    express_prompt = prompts.get("express_alert") or (
                        "Ты — высококлассный геополитический аналитик разведцентра «Агент Сноуден» (2026 год). "
                        "Выдели главное зерно заявления Дмитрия Медведева в 2-3 емких предложениях. "
                        "Не используй шаблонные заглушки, повторные цитаты или фиктивные ссылки на источники."
                    )
                    res = await self.builder.router.generate_response(
                        task="chat",
                        user_prompt=ai_prompt,
                        override_system_prompt=express_prompt
                    )
                    if res.get("success"):
                        raw_analysis = res.get("content", "").strip()
                        # Очищаем от шаблонных галлюцинаций моделей (@канал, [@канал](ссылка), повторных blockquote)
                        raw_analysis = re.sub(r'<blockquote[^>]*>.*?</blockquote>\s*—?\s*@?[а-яa-z0-9_-]*', '', raw_analysis, flags=re.DOTALL | re.IGNORECASE)
                        raw_analysis = re.sub(r'<blockquote[^>]*>.*?</blockquote>', '', raw_analysis, flags=re.DOTALL)
                        raw_analysis = re.sub(r'—?\s*Источники:\s*\[?@?канал\d*\]?\(?[^\)\n]*\)?', '', raw_analysis, flags=re.IGNORECASE)
                        raw_analysis = re.sub(r'\[@?канал\d*\]\((?:ссылка|url\d*)\)', '', raw_analysis, flags=re.IGNORECASE)
                        raw_analysis = re.sub(r'—?\s*@канал\b', '', raw_analysis, flags=re.IGNORECASE)
                        raw_analysis = re.sub(r'\n{3,}', '\n\n', raw_analysis)
                        ai_analysis = raw_analysis.strip()
                except Exception as ai_err:
                    logger.warning(f"ИИ-анализ поста Медведева: {ai_err}")

                msg_text = (
                    "⚡️ <b>Дмитрий Анатольевич вещает!</b>\n\n"
                    f"<blockquote>{html.escape(content)}</blockquote>\n\n"
                )
                if ai_analysis:
                    msg_text += f"🧠 <b>Анализ ИИ:</b>\n<i>{html.escape(ai_analysis)}</i>\n\n"

                msg_text += f"🔗 <a href=\"{url}\">Оригинальный пост в канале @medvedev_telegram</a>"

                photo_file = None
                if it.get("image_url"):
                    photo_file = await download_image_as_input_file(it["image_url"])

                from src.bot.handlers import split_telegram_html
                chunks = split_telegram_html(msg_text, max_chunk=3800)

                if photo_file and len(chunks) == 1 and len(chunks[0]) <= 1024:
                    await self.bot.send_photo(target_admin, photo=photo_file, caption=chunks[0], parse_mode="HTML")
                else:
                    if photo_file:
                        try:
                            await self.bot.send_photo(target_admin, photo=photo_file)
                        except Exception:
                            pass
                    for chunk in chunks:
                        await self.bot.send_message(target_admin, chunk, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Ошибка в трекере @medvedev_telegram: {e}")

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

        # Фоновый мониторинг публикаций Дмитрия Медведева (каждые 3 минуты)
        self.scheduler.add_job(
            self.check_medvedev_channel,
            'interval',
            minutes=3,
            id='medvedev_telegram_tracker'
        )

        self.scheduler.start()
        logger.info(f"Планировщик запущен. Расписание дайджеста: {self.config.digest_cron} (Часовой пояс: {self.timezone_name})")

    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown()
