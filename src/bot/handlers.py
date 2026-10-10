import logging
import os
import re
import html
import httpx
from pathlib import Path
from datetime import datetime
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    CallbackQuery,
    MessageReactionUpdated,
    FSInputFile,
    LinkPreviewOptions,
    InputMediaPhoto,
    BufferedInputFile
)
from aiogram.exceptions import TelegramBadRequest

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
                return BufferedInputFile(resp.content, filename=f"preview{ext}")
    except Exception as e:
        logger.warning(f"Не удалось загрузить изображение {url}: {e}")
    return None

import asyncio

async def download_images_as_media_group(urls: List[str], max_count: int = 6) -> List[InputMediaPhoto]:
    """Скачивает до max_count изображений из разных каналов и формирует альбом для отправки одним сообщением"""
    if not urls:
        return []
    tasks = [download_image_as_input_file(u) for u in urls[:max_count]]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    media_items = []
    for r in results:
        if isinstance(r, BufferedInputFile):
            media_items.append(InputMediaPhoto(media=r))
    return media_items

from src.llm.router import LLMRouter
from src.llm.prompts import markdown_to_telegram_html
from src.pipeline.digest_builder import DigestBuilder
from src.pipeline.storage import Storage
from src.pipeline.sources_manager import (
    get_telegram_channels,
    add_telegram_channel,
    remove_telegram_channel,
    get_rss_feeds,
    add_rss_feed,
    remove_rss_feed,
    clean_channel_username,
    determine_channel_category_with_ai,
    determine_rss_category_with_ai
)
from src.bot.keyboards import (
    get_main_menu_keyboard,
    get_chat_control_keyboard,
    get_digest_feedback_keyboard,
    get_provider_selection_keyboard,
    get_models_keyboard,
    get_provider_browser_keyboard,
    get_sources_menu_keyboard,
    get_delete_channels_keyboard,
    get_delete_rss_keyboard,
    get_category_selection_keyboard,
    get_cancel_keyboard
)

logger = logging.getLogger(__name__)

class SourceStates(StatesGroup):
    waiting_for_channel = State()
    waiting_for_rss_url = State()

CATEGORIES_MAP = {
    "ai": "Нейросети и ИИ",
    "devops": "DevOps & Self-Hosted",
    "dev": "Разработка и Кодинг",
    "gpu": "Hardware & GPU",
    "general": "Общее и Новости"
}

def setup_router(digest_builder: DigestBuilder, llm_router: LLMRouter, storage: Storage, admin_id: int, scheduler=None) -> Router:
    r = Router()

    # Проверка сохраненного admin_id в SQLite
    saved_admin = storage.get_setting("admin_id")
    configured_admin_id = int(saved_admin) if saved_admin and saved_admin.isdigit() else admin_id

    # Если admin_id равен 123456789 (шаблонный плейсхолдер из примера) — считаем ненастроенным (0)
    if configured_admin_id == 123456789:
        configured_admin_id = 0

    def is_admin(user_id: int) -> bool:
        nonlocal configured_admin_id
        # Авто-привязка первого написавшего пользователя как администратора
        if configured_admin_id == 0:
            configured_admin_id = user_id
            storage.set_setting("admin_id", str(user_id))
            logger.info(f"🔑 Администратор бота успешно авто-зарегистрирован: ID={user_id}")
            return True
        return user_id == configured_admin_id

    def get_web_app_url() -> str | None:
        url = os.getenv("WEB_APP_URL", "").strip()
        return url or "http://192.168.0.169:8000"

    async def safe_edit_text(call: CallbackQuery, text: str, reply_markup=None):
        try:
            await call.answer()
        except Exception:
            pass

        html_text = markdown_to_telegram_html(text)
        try:
            await call.message.edit_text(html_text, parse_mode="HTML", reply_markup=reply_markup)
        except TelegramBadRequest as e:
            if "message is not modified" in str(e).lower():
                return
            logger.warning(f"Ошибка редактирования сообщения HTML: {e}, попытка без разметки...")
            try:
                await call.message.edit_text(text, reply_markup=reply_markup)
            except Exception as e2:
                logger.error(f"Редактирование с кнопками не удалось ({e2}), пробуем без кнопок...")
                try:
                    await call.message.edit_text(text)
                except Exception as e3:
                    logger.error(f"Критическая ошибка edit_text: {e3}")
        except Exception as e:
            logger.error(f"Необработанная ошибка при edit_text: {e}")

    async def send_safe_reply(msg: Message, text: str, reply_markup=None, link_preview_options: LinkPreviewOptions = None):
        html_text = markdown_to_telegram_html(text)
        try:
            if len(html_text) > 4000:
                for x in range(0, len(html_text), 4000):
                    await msg.answer(html_text[x:x+4000], parse_mode="HTML")
                if reply_markup:
                    await msg.answer("💬 <b>Управление:</b>", parse_mode="HTML", reply_markup=reply_markup)
            else:
                await msg.answer(
                    html_text,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                    link_preview_options=link_preview_options
                )
        except TelegramBadRequest as e:
            logger.warning(f"Telegram HTML parse error ({e}), retrying as plain text...")
            try:
                if len(text) > 4000:
                    for x in range(0, len(text), 4000):
                        await msg.answer(text[x:x+4000])
                    if reply_markup:
                        await msg.answer("💬 Управление:", reply_markup=reply_markup)
                else:
                    await msg.answer(text, reply_markup=reply_markup)
            except Exception as e2:
                logger.error(f"Send with reply_markup failed ({e2}), retrying plain message...")
                try:
                    await msg.answer(text)
                except Exception as e3:
                    logger.error(f"Fatal send_safe_reply error: {e3}")
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            try:
                await msg.answer(f"⚠️ Ошибка отправки: {e}")
            except Exception:
                pass

    # --------------------------------------------------------------------------
    # Главное меню (Команда /start или кнопка "Главное меню", а также слова "старт", "меню")
    # --------------------------------------------------------------------------
    @r.message(Command("start", "menu", "help"))
    @r.message(F.text.lower().in_(["старт", "start", "меню", "menu", "привет", "главное меню", "/start", "/menu", "/help"]))
    async def cmd_start_msg(msg: Message, state: FSMContext):
        logger.info(f"📩 Команда СТАРТ/МЕНЮ от пользователя ID={msg.from_user.id} (@{msg.from_user.username})")
        if not is_admin(msg.from_user.id):
            await msg.answer(
                f"⛔️ <b>Доступ ограничен.</b>\n\n"
                f"Ваш Telegram ID: <code>{msg.from_user.id}</code>\n"
                f"ID администратора бота: <code>{configured_admin_id}</code>\n\n"
                f"Если это ваш бот, укажите:\n"
                f"<code>TELEGRAM_ADMIN_ID={msg.from_user.id}</code>\n"
                f"в файле <code>.env</code> на сервере и перезапустите контейнер.",
                parse_mode="HTML"
            )
            return

        await state.clear()
        is_local_on = await llm_router.check_local_health()
        local_status = "🟢 В сети (LM Studio)" if is_local_on else "🔴 Офлайн (Включен облачный резерв)"

        active_prov = llm_router.active_provider
        current_model = llm_router.get_current_model_for_provider(active_prov if active_prov != "auto" else "openrouter")
        chat_active = storage.is_chat_mode_active()
        tz_name = scheduler.timezone_name if scheduler else "Europe/Moscow"
        next_run = scheduler.get_next_run_time() if scheduler else "08:50"

        text = (
            f"👋 *Привет! Я твой автономный ИИ-хаб (2026).*\n\n"
            f"💻 *Ноутбук-сервер:* 🟢 24/7 Активен\n"
            f"🖥 *Основной ПК (LM Studio):* {local_status}\n"
            f"🧠 *Активный провайдер:* `{active_prov.upper()}`\n"
            f"🎯 *Текущая модель:* `{current_model}`\n"
            f"⏰ *Расписание:* `08:50 ({tz_name})`\n"
            f"⏳ *Следующая сводка:* `{next_run}`\n"
            f"💬 *Режим чата:* {'🟢 ВКЛЮЧЕН' if chat_active else '⚪️ Выключен'}\n\n"
            f"Используйте кнопки ниже для управления:"
        )
        await send_safe_reply(msg, text, reply_markup=get_main_menu_keyboard(chat_active, get_web_app_url()))

    @r.callback_query(F.data == "btn_menu")
    async def cb_main_menu(call: CallbackQuery, state: FSMContext):
        await call.answer()
        await state.clear()
        is_local_on = await llm_router.check_local_health()
        local_status = "🟢 В сети (LM Studio)" if is_local_on else "🔴 Офлайн"
        active_prov = llm_router.active_provider
        current_model = llm_router.get_current_model_for_provider(active_prov if active_prov != "auto" else "openrouter")
        chat_active = storage.is_chat_mode_active()

        text = (
            f"🏠 *Главное меню управления ИИ-хабом:*\n\n"
            f"💻 *Ноутбук:* 🟢 24/7 | 🖥 *ПК (LM Studio):* {local_status}\n"
            f"🧠 *Провайдер:* `{active_prov.upper()}` (`{current_model}`)\n"
            f"⏰ *Таймер сводки:* `08:50`\n"
            f"💬 *Режим чата:* {'🟢 ВКЛЮЧЕН' if chat_active else '⚪️ Выключен'}"
        )
        await safe_edit_text(call, text, get_main_menu_keyboard(chat_active, get_web_app_url()))

    # --------------------------------------------------------------------------
    # Управление режимом диалога (Включение / Выход / Очистка контекста)
    # --------------------------------------------------------------------------
    @r.message(Command("chat"))
    @r.callback_query(F.data == "btn_chat_start")
    async def cmd_chat_start(event: Message | CallbackQuery, state: FSMContext):
        await state.clear()
        storage.set_chat_mode(True)
        active_prov = llm_router.active_provider
        active_model = llm_router.get_current_model_for_provider(active_prov if active_prov != "auto" else "openrouter")

        text = (
            f"💬 *Режим диалога с ИИ активирован!*\n\n"
            f"Нейросеть: *{active_prov.upper()}* (`{active_model}`).\n\n"
            f"• Модель знает текущую дату (2026 год) и контекст последних собранных новостей.\n"
            f"• Просто пишите сообщения в чат. Чтобы завершить диалог, нажмите кнопку ниже или введите /exit."
        )

        if isinstance(event, CallbackQuery):
            await event.answer("Режим диалога включен")
            await safe_edit_text(event, text, get_chat_control_keyboard())
        else:
            await send_safe_reply(event, text, reply_markup=get_chat_control_keyboard())

    @r.message(Command("exit"))
    @r.callback_query(F.data == "btn_chat_stop")
    async def cmd_chat_stop(event: Message | CallbackQuery, state: FSMContext):
        await state.clear()
        storage.set_chat_mode(False)
        text = (
            f"🔴 *Режим диалога завершен.*\n\n"
            f"Вы вернулись в стандартный режим. Сообщения не будут пересылаться в LLM, пока вы снова не активируете чат."
        )
        if isinstance(event, CallbackQuery):
            await event.answer("Режим диалога выключен")
            await safe_edit_text(event, text, get_main_menu_keyboard(chat_mode=False, web_app_url=get_web_app_url()))
        else:
            await send_safe_reply(event, text, reply_markup=get_main_menu_keyboard(chat_mode=False, web_app_url=get_web_app_url()))

    @r.callback_query(F.data == "btn_chat_clear")
    async def cb_chat_clear(call: CallbackQuery):
        storage.clear_chat_history()
        await call.answer("🧹 Память диалога очищена!", show_alert=True)

    # --------------------------------------------------------------------------
    # Ручной запуск дайджеста (Создает нативный сворачиваемый пост + файл)
    # --------------------------------------------------------------------------
    @r.message(Command("digest"))
    @r.callback_query(F.data == "btn_run_digest")
    async def cmd_digest(event: Message | CallbackQuery):
        user_id = event.from_user.id
        logger.info(f"⚡️ Запуск дайджеста от ID={user_id}")
        if not is_admin(user_id):
            if isinstance(event, CallbackQuery):
                await event.answer("⛔️ Доступ ограничен", show_alert=True)
            else:
                await event.answer(f"⛔️ Доступ ограничен. Ваш ID: {user_id}")
            return

        msg = event if isinstance(event, Message) else event.message
        if isinstance(event, CallbackQuery):
            await event.answer("Сбор публикаций и генерация дайджеста...")

        wait_msg = await msg.answer("⏳ <i>Идет сбор новостей и формирование аналитического отчета...</i>", parse_mode="HTML")

        res = await digest_builder.generate_digest()
        try:
            await wait_msg.delete()
        except Exception:
            pass

        if not res.get("success"):
            await send_safe_reply(msg, f"⚠️ {res.get('text')}")
            return

        post_html = res.get("telegram_post_html") or res.get("text")
        lead_img = res.get("lead_image_url")
        html_path = res.get("full_report_html_path")
        md_path = res.get("full_report_md_path")
        md_file_id = Path(md_path).name if md_path else None

        # Кнопки под постом: Оценка, Чат, Окно в TG, Скачать MD
        kb = get_digest_feedback_keyboard(web_app_url=get_web_app_url(), md_file_id=md_file_id)

        # 1. Загружаем и отправляем альбом (медиагруппу) из разных фото/видео источников одним сообщением-коллажем!
        source_images = res.get("source_images") or ([lead_img] if lead_img else [])
        media_group = await download_images_as_media_group(source_images, max_count=6)

        if len(media_group) >= 2:
            try:
                # Отправляем единый альбом (коллаж до 6 фото/видео) из разных источников!
                await msg.answer_media_group(media=media_group)
            except Exception as e:
                logger.warning(f"Не удалось отправить медиагруппу источников ({e}), пробуем одиночное фото...")
                if media_group:
                    try:
                        await msg.answer_photo(photo=media_group[0].media)
                    except Exception:
                        pass
        elif len(media_group) == 1:
            try:
                await msg.answer_photo(photo=media_group[0].media)
            except Exception as e:
                logger.warning(f"Ошибка отправки фото: {e}")
        elif lead_img and lead_img.startswith("http"):
            try:
                await msg.answer_photo(photo=lead_img)
            except Exception:
                pass

        # 2. Отправляем структурированный аналитический пост с интерактивными кнопками
        await send_safe_reply(msg, post_html, reply_markup=kb)

        # 3. Отправляем полноценный .MD файл прямо в Telegram
        if md_path and os.path.exists(md_path):
            today_str = datetime.now().strftime("%d.%m.%Y")
            doc = FSInputFile(md_path, filename=f"Digest_{today_str}.md")
            await msg.answer_document(
                document=doc,
                caption="📑 <b>Полный аналитический отчет со всеми таблицами</b>\n<i>Нажмите на файл — откроется прямо в Telegram со всеми деталями.</i>",
                parse_mode="HTML"
            )

    # --------------------------------------------------------------------------
    # Просмотр свежих публикаций ленты с фотографиями: /feed или /news
    # --------------------------------------------------------------------------
    @r.message(Command("feed", "news"))
    async def cmd_feed_posts(msg: Message):
        if not is_admin(msg.from_user.id):
            return

        items = storage.get_news_feed(limit=5)
        if not items:
            await send_safe_reply(msg, "📭 Лента пуста. Нажмите /digest для сбора свежих публикаций.")
            return

        for it in items:
            ch = it.get("channel", "Канал")
            cat = it.get("category", "Новости")
            title = it.get("title", "")
            content = it.get("content", "")
            url = it.get("url", "")
            img_url = it.get("image_url", "")

            caption = (
                f"📢 <b>{html.escape(ch)}</b> • <i>{html.escape(cat)}</i>\n\n"
                f"<b>{html.escape(title)}</b>\n\n"
                f"{html.escape(content[:400])}\n\n"
            )
            if url:
                caption += f"🔗 <a href=\"{url}\">Первоисточник</a>"

            photo_file = None
            if img_url and img_url.startswith("http"):
                photo_file = await download_image_as_input_file(img_url)

            if photo_file:
                try:
                    await msg.answer_photo(photo=photo_file, caption=caption, parse_mode="HTML")
                    continue
                except Exception as e:
                    logger.debug(f"Ошибка отправки фото поста ленты: {e}")

            await send_safe_reply(msg, caption)

    # --------------------------------------------------------------------------
    # Ручная проверка новых видео RossName на YouTube: /checkross или /checkvideo
    # --------------------------------------------------------------------------
    @r.message(Command("checkross", "checkvideo"))
    async def cmd_check_ross_yt(msg: Message):
        if not is_admin(msg.from_user.id):
            return
        status = await send_safe_reply(msg, "🔍 <i>Проверяю канал @ross_name на новые YouTube-видео...</i>")
        if scheduler:
            await scheduler.check_ross_name_youtube()
            try:
                await status.delete()
            except Exception:
                pass
            await send_safe_reply(msg, "✅ <b>Проверка завершена!</b>\nЕсли были опубликованы новые ночные/утренние видео, уведомление пришло сообщением выше.")
        else:
            await send_safe_reply(msg, "⚠️ Планировщик фоновых задач не инициализирован.")

    # --------------------------------------------------------------------------
    # Очистка базы и архивов: /clearnews или /purge
    # --------------------------------------------------------------------------
    @r.message(Command("clearnews", "purge"))
    async def cmd_purge_data(msg: Message):
        if not is_admin(msg.from_user.id):
            return
        storage.purge_all_news_and_digests()
        for p in Path("data/digests").glob("*.*"):
            try:
                p.unlink()
            except Exception:
                pass
        await send_safe_reply(
            msg,
            "🧹 <b>База новостей и архивы дайджестов полностью очищены!</b>\n\n"
            "Все тестовые данные удалены. Система начнет следующий сбор с чистого листа по обновленному списку источников."
        )

    # --------------------------------------------------------------------------
    # Тумблер тестового режима (не сохранять в БД во время тестов): /testmode
    # --------------------------------------------------------------------------
    @r.message(Command("testmode"))
    async def cmd_toggle_testmode(msg: Message):
        if not is_admin(msg.from_user.id):
            return
        args = msg.text.strip().split()
        if len(args) > 1:
            val = args[1].lower()
            if val in ["1", "on", "true", "вкл"]:
                storage.set_test_mode(True)
            elif val in ["0", "off", "false", "выкл"]:
                storage.set_test_mode(False)
        else:
            current = storage.is_test_mode()
            storage.set_test_mode(not current)

        now_mode = storage.is_test_mode()
        status_text = (
            "🟢 <b>ВКЛЮЧЕН (1 / True)</b>\n"
            "<i>Новости НЕ помечаются прочитанными и НЕ сохраняются в постоянную базу. "
            "Дайджесты и чат не засоряют диск. Можно тестировать бесконечно!</i>"
        ) if now_mode else (
            "⚪️ <b>ВЫКЛЮЧЕН (0 / False)</b>\n"
            "<i>Боевой режим: новости помечаются как прочитанные и архивируются в постоянную БД.</i>"
        )
        await send_safe_reply(
            msg,
            f"⚙️ <b>Тумблер тестового режима:</b>\n\n{status_text}\n\n"
            f"Команды: <code>/testmode on</code> или <code>/testmode off</code>"
        )

    # --------------------------------------------------------------------------
    # Скачивание исходного Markdown файла дайджеста
    # --------------------------------------------------------------------------
    @r.callback_query(F.data.startswith("getmd__"))
    async def cb_download_md(call: CallbackQuery):
        md_filename = call.data.replace("getmd__", "")
        md_file_path = Path("data/digests") / md_filename
        if md_file_path.exists():
            await call.answer("Отправка файла...")
            doc = FSInputFile(str(md_file_path), filename=md_filename)
            await call.message.answer_document(
                document=doc,
                caption="📄 <b>Исходный Markdown-файл дайджеста</b>",
                parse_mode="HTML"
            )
        else:
            await call.answer("Файл не найден на сервере", show_alert=True)

    # --------------------------------------------------------------------------
    # Оценка дайджеста (Обратная связь и обучение предпочтений)
    # --------------------------------------------------------------------------
    @r.callback_query(F.data.in_(["fb_like", "fb_dislike"]))
    async def on_digest_feedback(call: CallbackQuery):
        is_like = call.data == "fb_like"
        context_snippet = call.message.text[:250] if call.message.text else "Дайджест"
        storage.record_feedback("like" if is_like else "dislike", context_snippet)

        alert_msg = "👍 Зафиксировано! Сохранено в user_learned_preferences.txt и учтется завтра." if is_like else "👎 Учтено! Снизим приоритет подобных тем в будущих сводках."
        await call.answer(alert_msg, show_alert=True)

    @r.message_reaction()
    async def on_reaction_updated(reaction_event: MessageReactionUpdated):
        """Перехват нативных реакций Telegram (эмодзи 👍, 👎, 🔥, ❤️)"""
        new_reactions = [r.emoji for r in reaction_event.new_reaction if hasattr(r, "emoji")]
        if not new_reactions:
            return

        emoji = new_reactions[0]
        context = "Реакция на сообщение"
        storage.record_feedback(emoji, context)
        logger.info(f"Зафиксирована нативная реакция пользователя: {emoji}")

    # --------------------------------------------------------------------------
    # Управление источниками новостей (Telegram каналы и RSS)
    # --------------------------------------------------------------------------
    @r.message(Command("sources"))
    @r.callback_query(F.data == "btn_sources")
    async def cmd_sources(event: Message | CallbackQuery, state: FSMContext):
        user_id = event.from_user.id
        logger.info(f"📡 Запрос меню источников от ID={user_id}")
        await state.clear()
        if not is_admin(user_id):
            if isinstance(event, CallbackQuery):
                await event.answer("⛔️ Доступ ограничен", show_alert=True)
            else:
                await send_safe_reply(event, f"⛔️ Доступ ограничен. Ваш ID: {user_id}")
            return

        tg_channels = get_telegram_channels()
        rss_feeds = get_rss_feeds()

        tg_lines = []
        for i, c in enumerate(tg_channels, 1):
            tg_lines.append(f"{i}. <a href=\"https://t.me/{c['username']}\">@{c['username']}</a> <i>({c.get('category', 'Общее')})</i>")
        tg_list = "\n".join(tg_lines) or "<i>(нет подключенных каналов)</i>"

        rss_lines = []
        for i, r in enumerate(rss_feeds, 1):
            rss_lines.append(f"{i}. <b>{r.get('name', 'RSS')}</b> <i>({r.get('category', 'Общее')})</i>\n   <code>{r.get('url')}</code>")
        rss_list = "\n".join(rss_lines) or "<i>(нет подключенных RSS)</i>"

        text = (
            f"📡 <b>Управление источниками новостей:</b>\n\n"
            f"📢 <b>Подключенные Telegram-каналы ({len(tg_channels)}):</b>\n{tg_list}\n\n"
            f"📰 <b>RSS-ленты ({len(rss_feeds)}):</b>\n{rss_list}\n\n"
            f"<i>Вы можете добавлять каналы и RSS через кнопки ниже или командами:</i>\n"
            f"• <code>/addchannel @username [категория]</code>\n"
            f"• <code>/delchannel @username</code>"
        )

        kb = get_sources_menu_keyboard()
        if isinstance(event, CallbackQuery):
            await event.answer()
            await safe_edit_text(event, text, kb)
        else:
            await send_safe_reply(event, text, reply_markup=kb)

    # 1. Добавление Telegram-канала через кнопку
    @r.callback_query(F.data == "btn_add_channel")
    async def cb_add_channel(call: CallbackQuery, state: FSMContext):
        await call.answer()
        await state.set_state(SourceStates.waiting_for_channel)
        text = (
            "📢 <b>Добавление Telegram-канала</b>\n\n"
            "Отправьте в чат <b>@юзернейм</b> канала или ссылку на него.\n\n"
            "<i>Примеры:</i>\n"
            "• <code>@xakep_ru</code>\n"
            "• <code>https://t.me/ai_newz</code>\n"
            "• <code>habr_com</code>\n\n"
            "<i>Канал должен быть публичным (с открытым веб-просмотром t.me/s/...).</i>"
        )
        await safe_edit_text(call, text, get_cancel_keyboard("btn_sources"))

    @r.message(SourceStates.waiting_for_channel)
    async def on_input_channel(msg: Message, state: FSMContext):
        if not is_admin(msg.from_user.id):
            return

        raw_input = msg.text.strip()
        if raw_input.startswith("/cancel"):
            await state.clear()
            await send_safe_reply(msg, "❌ Добавление канала отменено.", reply_markup=get_sources_menu_keyboard())
            return

        clean_user = clean_channel_username(raw_input)
        if not clean_user or not re.match(r'^[a-zA-Z0-9_]{3,35}$', clean_user):
            await send_safe_reply(
                msg,
                "❌ Некорректный юзернейм канала. Допустимы только буквы A-Z, цифры и _ (например, <code>@ai_newz</code>).\n"
                "Попробуйте еще раз или нажмите отмену:",
                reply_markup=get_cancel_keyboard("btn_sources")
            )
            return

        status_msg = await send_safe_reply(msg, f"⏳ <i>Анализирую канал @{clean_user} и его последние посты через ИИ...</i>")
        ai_cat = await determine_channel_category_with_ai(clean_user, llm_router)
        try:
            await status_msg.delete()
        except Exception:
            pass

        await state.update_data(clean_username=clean_user, ai_category=ai_cat)
        text = (
            f"📢 Канал <b>@{clean_user}</b> распознан!\n\n"
            f"🤖 <i>ИИ определил категорию по недавним публикациям:</i>\n"
            f"👉 «<b>{ai_cat}</b>»\n\n"
            f"Подтвердите категорию или выберите другую:"
        )
        await send_safe_reply(msg, text, reply_markup=get_category_selection_keyboard(clean_user, ai_cat))

    @r.callback_query(F.data.startswith("setchcat__"))
    async def on_channel_category_selected(call: CallbackQuery, state: FSMContext):
        parts = call.data.split("__")
        if len(parts) < 3:
            await call.answer("Ошибка параметров", show_alert=True)
            return

        username = parts[1]
        cat_slug = parts[2]

        state_data = await state.get_data()
        await state.clear()

        if cat_slug == "ai_detected":
            cat_title = state_data.get("ai_category", "Общее и Новости")
        else:
            cat_title = CATEGORIES_MAP.get(cat_slug, "Общее и Новости")

        success, result_msg = add_telegram_channel(username, cat_title)
        await call.answer("Канал добавлен!" if success else "Внимание", show_alert=not success)

        text = (
            f"{'🎉' if success else '⚠️'} {result_msg}\n\n"
            f"🏷 Категория: «<b>{cat_title}</b>»\n"
            f"Теперь публикации из <b>@{username}</b> будут анализироваться для утренней сводки и Discover-ленты."
        )
        await safe_edit_text(call, text, get_sources_menu_keyboard())

    # 2. Удаление Telegram-канала через кнопку
    @r.callback_query(F.data == "btn_del_channel_menu")
    async def cb_del_channel_menu(call: CallbackQuery):
        await call.answer()
        channels = get_telegram_channels()
        if not channels:
            await call.answer("Список каналов пуст", show_alert=True)
            return

        text = "🗑 <b>Выберите Telegram-канал для удаления из источников:</b>"
        await safe_edit_text(call, text, get_delete_channels_keyboard(channels))

    @r.callback_query(F.data.startswith("delch__"))
    async def on_delete_channel(call: CallbackQuery):
        username = call.data.replace("delch__", "")
        success, result_msg = remove_telegram_channel(username)
        await call.answer(f"Канал @{username} удален!" if success else "Ошибка", show_alert=True)

        channels = get_telegram_channels()
        if channels:
            text = f"🗑 Канал <b>@{username}</b> удален.\n\nВыберите еще канал для удаления или вернитесь назад:"
            await safe_edit_text(call, text, get_delete_channels_keyboard(channels))
        else:
            text = "✅ Все Telegram-каналы удалены из списка источников."
            await safe_edit_text(call, text, get_sources_menu_keyboard())

    # 3. Добавление и удаление RSS
    @r.callback_query(F.data == "btn_add_rss")
    async def cb_add_rss(call: CallbackQuery, state: FSMContext):
        await call.answer()
        await state.set_state(SourceStates.waiting_for_rss_url)
        text = (
            "📰 <b>Добавление RSS-ленты</b>\n\n"
            "Отправьте в чат URL-адрес RSS потока.\n\n"
            "<i>Пример:</i> <code>https://3dnews.ru/news/rss/</code>"
        )
        await safe_edit_text(call, text, get_cancel_keyboard("btn_sources"))

    @r.message(SourceStates.waiting_for_rss_url)
    async def on_input_rss(msg: Message, state: FSMContext):
        if not is_admin(msg.from_user.id):
            return

        raw_url = msg.text.strip()
        if raw_url.startswith("/cancel"):
            await state.clear()
            await send_safe_reply(msg, "❌ Добавление RSS отменено.", reply_markup=get_sources_menu_keyboard())
            return

        status_msg = await send_safe_reply(msg, "⏳ <i>Анализирую RSS-поток через ИИ...</i>")
        ai_cat = await determine_rss_category_with_ai(raw_url, llm_router)
        try:
            await status_msg.delete()
        except Exception:
            pass

        await state.clear()
        success, result_msg = add_rss_feed(raw_url, category=ai_cat)
        await send_safe_reply(
            msg,
            f"{'🎉' if success else '⚠️'} {result_msg}\n\n🤖 <i>Определена категория:</i> «<b>{ai_cat}</b>»",
            reply_markup=get_sources_menu_keyboard()
        )

    @r.callback_query(F.data == "btn_del_rss_menu")
    async def cb_del_rss_menu(call: CallbackQuery):
        await call.answer()
        feeds = get_rss_feeds()
        if not feeds:
            await call.answer("Список RSS пуст", show_alert=True)
            return

        text = "🗑 <b>Выберите RSS-ленту для удаления:</b>"
        await safe_edit_text(call, text, get_delete_rss_keyboard(feeds))

    @r.callback_query(F.data.startswith("delrss__"))
    async def on_delete_rss(call: CallbackQuery):
        idx_str = call.data.replace("delrss__", "")
        feeds = get_rss_feeds()
        try:
            idx = int(idx_str)
            if 0 <= idx < len(feeds):
                target_url = feeds[idx]["url"]
                remove_rss_feed(target_url)
                await call.answer("RSS-лента удалена!", show_alert=True)
        except Exception as e:
            logger.error(f"Error removing RSS: {e}")

        remaining = get_rss_feeds()
        if remaining:
            text = "🗑 RSS-лента удалена.\n\nВыберите еще одну или вернитесь назад:"
            await safe_edit_text(call, text, get_delete_rss_keyboard(remaining))
        else:
            text = "✅ Все RSS-ленты удалены."
            await safe_edit_text(call, text, get_sources_menu_keyboard())

    # 4. Быстрые команды для прямого добавления/удаления каналов и RSS
    @r.message(Command("addchannel"))
    async def cmd_quick_addchannel(msg: Message):
        if not is_admin(msg.from_user.id):
            return

        parts = msg.text.strip().split(maxsplit=2)
        if len(parts) < 2:
            await send_safe_reply(
                msg,
                "ℹ️ <b>Использование команды:</b>\n"
                "<code>/addchannel @username [Категория]</code>\n\n"
                "<i>Примеры:</i>\n"
                "• <code>/addchannel @xakep_ru</code> (ИИ автоматически определит категорию!)\n"
                "• <code>/addchannel @habr_com DevOps & Linux</code>\n"
                "• <code>/addchannel https://t.me/ai_newz</code>"
            )
            return

        username = parts[1]
        if len(parts) > 2:
            category = parts[2]
        else:
            status_msg = await send_safe_reply(msg, f"⏳ <i>Анализирую канал {username} через ИИ...</i>")
            category = await determine_channel_category_with_ai(username, llm_router)
            try:
                await status_msg.delete()
            except Exception:
                pass

        success, result_msg = add_telegram_channel(username, category)
        await send_safe_reply(
            msg,
            f"{'🎉' if success else '⚠️'} {result_msg}\n\n🤖 <i>Категория:</i> «<b>{category}</b>»",
            reply_markup=get_sources_menu_keyboard()
        )

    @r.message(Command("delchannel"))
    async def cmd_quick_delchannel(msg: Message):
        if not is_admin(msg.from_user.id):
            return

        parts = msg.text.strip().split()
        if len(parts) < 2:
            await send_safe_reply(msg, "ℹ️ <b>Использование:</b> <code>/delchannel @username</code>")
            return

        username = parts[1]
        success, result_msg = remove_telegram_channel(username)
        await send_safe_reply(msg, f"{'🗑' if success else '⚠️'} {result_msg}", reply_markup=get_sources_menu_keyboard())

    @r.message(Command("addrss"))
    async def cmd_quick_addrss(msg: Message):
        if not is_admin(msg.from_user.id):
            return

        parts = msg.text.strip().split(maxsplit=2)
        if len(parts) < 2:
            await send_safe_reply(
                msg,
                "ℹ️ <b>Использование команды:</b>\n"
                "<code>/addrss &lt;URL&gt; [Категория]</code>\n\n"
                "<i>Примеры:</i>\n"
                "• <code>/addrss https://3dnews.ru/news/rss/</code> (ИИ определит категорию автоматически)\n"
                "• <code>/addrss https://habr.com/ru/rss/hubs/all/ Hardware</code>"
            )
            return

        url = parts[1]
        if len(parts) > 2:
            category = parts[2]
        else:
            status_msg = await send_safe_reply(msg, f"⏳ <i>Анализирую RSS-поток через ИИ...</i>")
            category = await determine_rss_category_with_ai(url, llm_router)
            try:
                await status_msg.delete()
            except Exception:
                pass

        success, result_msg = add_rss_feed(url, category=category)
        await send_safe_reply(
            msg,
            f"{'🎉' if success else '⚠️'} {result_msg}\n\n🤖 <i>Категория:</i> «<b>{category}</b>»",
            reply_markup=get_sources_menu_keyboard()
        )

    @r.message(Command("delrss"))
    async def cmd_quick_delrss(msg: Message):
        if not is_admin(msg.from_user.id):
            return

        parts = msg.text.strip().split()
        if len(parts) < 2:
            await send_safe_reply(msg, "ℹ️ <b>Использование:</b> <code>/delrss &lt;URL&gt;</code>")
            return

        url = parts[1]
        success, result_msg = remove_rss_feed(url)
        await send_safe_reply(msg, f"{'🗑' if success else '⚠️'} {result_msg}", reply_markup=get_sources_menu_keyboard())

    # --------------------------------------------------------------------------
    # Выбор провайдеров и моделей (редактирование на месте)
    # --------------------------------------------------------------------------
    @r.message(Command("model"))
    @r.callback_query(F.data == "btn_select_provider")
    async def cmd_model(event: Message | CallbackQuery):
        user_id = event.from_user.id
        logger.info(f"🧠 Запрос выбора провайдера от ID={user_id}")
        if not is_admin(user_id):
            if isinstance(event, CallbackQuery):
                await event.answer("⛔️ Доступ ограничен", show_alert=True)
            else:
                await send_safe_reply(event, f"⛔️ Доступ ограничен. Ваш ID: {user_id}")
            return

        kb = get_provider_selection_keyboard(llm_router.active_provider)
        text = "🧠 *Выберите активного ИИ-провайдера:*"

        if isinstance(event, CallbackQuery):
            await event.answer()
            await safe_edit_text(event, text, kb)
        else:
            await send_safe_reply(event, text, reply_markup=kb)

    @r.callback_query(F.data.startswith("prov_"))
    async def on_provider_selected(call: CallbackQuery):
        new_provider = call.data.replace("prov_", "")
        llm_router.set_provider(new_provider)
        storage.set_setting("active_provider", new_provider)

        await call.answer(f"Выбран: {new_provider.upper()}")
        kb = get_provider_selection_keyboard(new_provider)
        await safe_edit_text(call, f"🧠 *Провайдер переключен на: {new_provider.upper()}*", kb)

    @r.callback_query(F.data == "btn_browse_models")
    async def on_browse_models(call: CallbackQuery):
        await call.answer()
        kb = get_provider_browser_keyboard()
        await safe_edit_text(call, "🎯 *Каталог моделей (включая бесплатные OpenRouter):*", kb)

    @r.callback_query(F.data.startswith("viewmods_"))
    async def on_view_models(call: CallbackQuery):
        prov = call.data.replace("viewmods_", "")
        await call.answer()
        curr_m = llm_router.get_current_model_for_provider(prov)
        kb = get_models_keyboard(prov, curr_m)
        await safe_edit_text(call, f"📋 *Выберите модель для {prov.upper()}:*", kb)

    @r.callback_query(F.data.startswith("setm_"))
    async def on_set_model(call: CallbackQuery):
        raw = call.data.replace("setm_", "")
        prov, escaped_model = raw.split("__", 1)
        model_id = escaped_model.replace("--", "/").replace("==", ":")

        llm_router.set_model(prov, model_id)
        storage.set_setting(f"model_{prov}", model_id)

        await call.answer(f"Установлена: {model_id}")
        kb = get_models_keyboard(prov, model_id)
        await safe_edit_text(call, f"✅ *Для {prov.upper()} активна модель:*\n`{model_id}`", kb)

    # --------------------------------------------------------------------------
    # Статус системы (Редактирование на месте)
    # --------------------------------------------------------------------------
    @r.message(Command("status"))
    @r.callback_query(F.data == "btn_status")
    async def cmd_status(event: Message | CallbackQuery):
        user_id = event.from_user.id
        logger.info(f"📊 Запрос статуса от ID={user_id}")
        if not is_admin(user_id):
            if isinstance(event, CallbackQuery):
                await event.answer("⛔️ Доступ ограничен", show_alert=True)
            else:
                await send_safe_reply(event, f"⛔️ Доступ ограничен. Ваш ID: {user_id}")
            return

        local_on = await llm_router.check_local_health()
        tz_name = scheduler.timezone_name if scheduler else "Europe/Moscow"
        next_run = scheduler.get_next_run_time() if scheduler else "08:50"

        text = (
            f"📊 *Статус узлов системы (2026):*\n\n"
            f"1. 💻 *Ноутбук (Сервер 24/7):* 🟢 В сети\n"
            f"   - Планировщик: `08:50 ({tz_name})`\n"
            f"   - Следующая сводка: `{next_run}`\n"
            f"   - База SQLite: OK\n\n"
            f"2. 🖥 *Основной ПК (LM Studio + Qwen 2.5):*\n"
            f"   - Статус сервера LM Studio: {'🟢 Доступен (порт 1234)' if local_on else '⚪️ Не отвечает (офлайн/сон)'}\n"
            f"   - Адрес: `{llm_router.config.local.base_url}`\n\n"
            f"3. ☁️ *Облачные шлюзы:*\n"
            f"   - GroqCloud: {'🟢 Подключен' if llm_router.config.groq.api_key else '⚪️ Нет ключа'}\n"
            f"   - Google Gemini: {'🟢 Подключен' if llm_router.config.gemini.api_key else '⚪️ Нет ключа'}\n"
            f"   - OpenRouter (~25 free моделей): {'🟢 Подключен' if llm_router.config.openrouter.api_key else '⚪️ Нет ключа'}\n\n"
            f"Текущий режим: *{llm_router.active_provider.upper()}*"
        )
        if isinstance(event, CallbackQuery):
            await event.answer()
            await safe_edit_text(event, text, get_main_menu_keyboard(storage.is_chat_mode_active(), get_web_app_url()))
        else:
            await send_safe_reply(event, text, reply_markup=get_main_menu_keyboard(storage.is_chat_mode_active(), get_web_app_url()))

    # --------------------------------------------------------------------------
    # Ручная установка любой модели: /setmodel <provider> <model_slug>
    # --------------------------------------------------------------------------
    @r.message(Command("setmodel"))
    async def cmd_setmodel(msg: Message):
        if not is_admin(msg.from_user.id):
            return

        parts = msg.text.strip().split()
        if len(parts) < 3:
            text = (
                "ℹ️ *Установка любой модели вручную:*\n"
                "`/setmodel <провайдер> <название_модели>`\n\n"
                "Примеры:\n"
                "• `/setmodel openrouter qwen/qwen-2.5-72b-instruct:free`\n"
                "• `/setmodel gemini gemini-2.0-flash`\n"
                "• `/setmodel groq llama-3.3-70b-versatile`\n"
                "• `/setmodel local qwen2.5-7b-instruct`"
            )
            await send_safe_reply(msg, text)
            return

        provider = parts[1].lower()
        model_name = parts[2]

        if provider not in ["local", "groq", "gemini", "openrouter"]:
            await send_safe_reply(msg, "❌ Допустимые провайдеры: `local`, `groq`, `gemini`, `openrouter`")
            return

        llm_router.set_model(provider, model_name)
        storage.set_setting(f"model_{provider}", model_name)
        await send_safe_reply(msg, f"✅ Для провайдера *{provider.upper()}* установлена модель:\n`{model_name}`")

    # --------------------------------------------------------------------------
    # ПРЯМОЙ ЧАТ С ИИ
    # --------------------------------------------------------------------------
    @r.message(F.text & ~F.text.startswith("/"))
    async def on_user_chat_message(msg: Message):
        logger.info(f"📩 Сообщение в чат от ID={msg.from_user.id}: {msg.text[:50]}")
        if not is_admin(msg.from_user.id):
            await msg.answer(
                f"⛔️ <b>Доступ ограничен.</b>\n\n"
                f"Ваш Telegram ID: <code>{msg.from_user.id}</code>\n"
                f"ID администратора бота: <code>{configured_admin_id}</code>\n\n"
                f"Если это ваш бот, укажите:\n"
                f"<code>TELEGRAM_ADMIN_ID={msg.from_user.id}</code>\n"
                f"в файле <code>.env</code> на сервере и перезапустите контейнер.",
                parse_mode="HTML"
            )
            return

        # Если режим чата выключен — подсказываем, как включить
        if not storage.is_chat_mode_active():
            text = (
                f"💡 *Режим диалога сейчас выключен.*\n\n"
                f"Чтобы задать вопрос ИИ или обсудить свежие новости, нажмите кнопку ниже или введите /chat."
            )
            await send_safe_reply(msg, text, reply_markup=get_main_menu_keyboard(chat_mode=False, web_app_url=get_web_app_url()))
            return

        await msg.bot.send_chat_action(msg.chat.id, "typing")
        user_query = msg.text

        # Извлекаем контекст: история, последний дайджест, свежие новости
        history = storage.get_chat_history(limit=6)
        latest_digest = storage.get_latest_digest()

        recent_items = storage.get_news_feed(limit=30)
        # Если в базе новостей мало или пусто — оперативно собираем свежие посты из каналов прямо сейчас!
        if len(recent_items) < 5:
            try:
                fresh = await digest_builder.collect_fresh_news()
                if fresh:
                    recent_items = storage.get_news_feed(limit=30) or fresh
            except Exception as e:
                logger.warning(f"Оперативный сбор новостей для чата: {e}")

        recent_news_str = ""
        if recent_items:
            news_parts = []
            for i, it in enumerate(recent_items[:25], 1):
                ch = it.get("channel", "Канал")
                cat = it.get("category", "Новости")
                t = it.get("title", "")
                c = it.get("content", "")[:500]
                u = it.get("url", "")
                news_parts.append(f"[{i}] Источник: {ch} | Категория: {cat}\nЗаголовок: {t}\nТекст: {c}\nСсылка: {u}")
            recent_news_str = "\n---\n".join(news_parts)

        try:
            res = await llm_router.generate_response(
                task="chat",
                user_prompt=user_query,
                chat_history=history,
                latest_digest=latest_digest,
                recent_news=recent_news_str
            )

            if not res.get("success"):
                await send_safe_reply(msg, f"⚠️ {res.get('content')}")
                return

            answer_text = res["content"]

            # Сохраняем ход беседы в память
            storage.add_chat_message("user", user_query)
            storage.add_chat_message("assistant", answer_text)

            footer = f"\n\n🤖 *{res.get('provider').upper()}* (`{res.get('model')}`) • ⏱ {res.get('latency')} сек."
            if res.get("fallback_occurred"):
                footer += " _(failover)_"

            await send_safe_reply(msg, answer_text + footer, reply_markup=get_chat_control_keyboard())

        except Exception as e:
            logger.exception(f"Unhandled error in chat handler: {e}")
            await send_safe_reply(msg, f"⚠️ Внутренняя ошибка обработчика: {e}")

    return r
