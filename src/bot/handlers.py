import logging
import os
import re
import html
import time
import httpx
from typing import List, Dict, Any, Optional
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
    BufferedInputFile,
    BotCommand,
    BotCommandScopeDefault
)
from aiogram.exceptions import TelegramBadRequest

BOT_COMMANDS = [
    BotCommand(command="start", description="🏠 Главное меню и статус"),
    BotCommand(command="chat", description="💬 Войти в диалог с ИИ"),
    BotCommand(command="exit", description="🚪 Выйти из диалога в меню"),
    BotCommand(command="digest", description="⚡️ Сформировать утренний дайджест"),
    BotCommand(command="search", description="🔍 Основательный поиск (Каналы + Web + Darknet)"),
    BotCommand(command="google", description="🌐 Вкл/выкл Google AI Search (Grounding)"),
    BotCommand(command="darknet", description="🧅 Вкл/выкл поиск в Darknet (.onion)"),
    BotCommand(command="status", description="📊 Статус узлов и моделей"),
    BotCommand(command="sources", description="📋 Управление каналами и RSS"),
    BotCommand(command="add_channel", description="➕ Добавить Telegram-канал"),
    BotCommand(command="add_rss", description="➕ Добавить RSS-ленту"),
    BotCommand(command="del_source", description="🗑 Удалить источник"),
    BotCommand(command="refresh", description="🔄 Собрать свежие новости сейчас"),
    BotCommand(command="find", description="🔎 Найти пост в архиве каналов"),
    BotCommand(command="test_mode", description="🧪 Переключить тестовый режим"),
    BotCommand(command="clearnews", description="🧹 Очистить базу новостей и дайджестов"),
    BotCommand(command="help", description="ℹ️ Инструкция и справка по командам"),
]

async def register_bot_commands(bot):
    try:
        await bot.set_my_commands(BOT_COMMANDS, scope=BotCommandScopeDefault())
        logger.info("Команды бота в меню успешно зарегистрированы.")
    except Exception as e:
        logger.warning(f"Ошибка регистрации команд в меню Telegram: {e}")

def extract_stems(text: str) -> List[str]:
    """Извлекает корни слов для семантического поиска по новостям"""
    words = re.findall(r'[а-яa-z0-9_-]+', text.lower())
    stems = []
    stop_words = {
        "что", "как", "где", "когда", "почему", "зачем", "это", "или", "для", "про", "был",
        "была", "были", "быть", "все", "всё", "его", "она", "они", "том", "под", "при",
        "над", "после", "этой", "этом", "этих", "какой", "какие", "расскажи", "пожалуйста",
        "слышно", "известно", "инфа", "инфу", "информацию", "данные", "собери", "подробно",
        "максимум", "хочу", "дай", "покажи"
    }
    for w in words:
        if len(w) <= 2 or w in stop_words:
            continue
        stem = re.sub(r'(?:овали|евали|ован|еван|али|яли|или|ила|ала|ели|ать|ять|ить|еть|ого|его|ому|ему|ыми|ими|ами|ями|ах|ях|ия|ие|ые|ое|ее|ом|ем|ой|ей|ам|ям|а|я|о|е|у|ю|ы|и)$', '', w)
        stems.append(stem if len(stem) >= 3 else w)
    return list(dict.fromkeys(stems))

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

class LiveProgressAnimator:
    """
    Фоновый аниматор живого прогресса в реальном времени для Telegram.
    Регулярно обновляет сообщение (переворачивающиеся песочные часы ⏳ ⌛️, секундомер, статус)
    каждые 1.6 сек без риска превышения лимитов Telegram.
    """
    def __init__(self, message: Message, initial_text: str = "Инициализация..."):
        self.message = message
        self.base_text = initial_text
        self.is_running = True
        self.start_time = time.time()
        self.task: Optional[asyncio.Task] = None
        self.frames = ["⏳", "⌛️"]
        self.frame_idx = 0

    def update_text(self, text: str):
        self.base_text = text

    async def _animate_loop(self):
        while self.is_running:
            self.frame_idx = (self.frame_idx + 1) % len(self.frames)
            icon = self.frames[self.frame_idx]
            elapsed = int(time.time() - self.start_time)

            # Очищаем base_text от ведущих иконок и статических спиннеров
            cleaned = re.sub(r'^[⏳⌛️\s\U0001F6E1\U0001F4E1\U0001F50D\U0001F9E0\U00002728\U0001F504]+', '', self.base_text)
            cleaned = re.sub(r'[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]', '', cleaned).strip()

            animated_text = f"{icon} {cleaned}\n\n<i>⏱ В процессе: {elapsed} сек.</i>"

            try:
                await self.message.edit_text(animated_text, parse_mode="HTML")
            except Exception:
                pass

            await asyncio.sleep(1.6)

    def start(self):
        self.task = asyncio.create_task(self._animate_loop())

    async def stop(self):
        self.is_running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

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
    get_cancel_keyboard,
    get_darknet_keyboard,
    get_search_settings_keyboard,
    get_search_result_keyboard
)

logger = logging.getLogger(__name__)

class SourceStates(StatesGroup):
    waiting_for_channel = State()
    waiting_for_rss_url = State()

CATEGORIES_MAP = {
    "soft": "OpenSource & Полезный софт",
    "svo": "Военно-политическая аналитика & СВО",
    "sec": "Кибербезопасность & IT",
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

def split_telegram_html(text: str, max_chunk: int = 3800) -> List[str]:
    """
    Интеллектуальное разделение длинных HTML сообщений для Telegram:
    - Разделяет строго по границам параграфов (\\n\\n) и новостных карточек.
    - Никогда не режет HTML-теги или слова посредине!
    - Гарантирует баланс открывающих и закрывающих тегов (<b>, <i>, <code>, <blockquote>) внутри каждого чанка.
    """
    if len(text) <= max_chunk:
        return [text]

    chunks = []
    paragraphs = text.split("\n\n")
    current_chunk = []
    current_len = 0

    for p in paragraphs:
        p_len = len(p) + 2
        if current_len + p_len > max_chunk:
            if current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = [p]
                current_len = len(p)
            else:
                lines = p.split("\n")
                sub_chunk = []
                sub_len = 0
                for line in lines:
                    l_len = len(line) + 1
                    if sub_len + l_len > max_chunk:
                        if sub_chunk:
                            chunks.append("\n".join(sub_chunk))
                            sub_chunk = [line]
                            sub_len = len(line)
                        else:
                            chunks.append(line[:max_chunk])
                            sub_chunk = [line[max_chunk:]]
                            sub_len = len(line[max_chunk:])
                    else:
                        sub_chunk.append(line)
                        sub_len += l_len
                if sub_chunk:
                    current_chunk = ["\n".join(sub_chunk)]
                    current_len = sub_len
        else:
            current_chunk.append(p)
            current_len += p_len

    if current_chunk:
        chunks.append("\n\n".join(current_chunk))

    balanced_chunks = []
    for chunk in chunks:
        # Проверяем баланс тегов Telegram
        for tag in ["b", "i", "code", "blockquote", "pre", "a"]:
            open_count = len(re.findall(rf"<{tag}(?:\s+[^>]*)?>", chunk, flags=re.IGNORECASE))
            close_count = len(re.findall(rf"</{tag}>", chunk, flags=re.IGNORECASE))
            if open_count > close_count:
                chunk += f"</{tag}>" * (open_count - close_count)
            elif close_count > open_count:
                chunk = f"<{tag}>" * (close_count - open_count) + chunk
        balanced_chunks.append(chunk)

    return balanced_chunks

    async def send_safe_reply(msg: Message, text: str, reply_markup=None, link_preview_options: LinkPreviewOptions = None):
        # Всегда конвертируем Markdown разметку (**жирный**, *курсив*, [ссылки](url)) в валидный Telegram HTML,
        # сохраняя при этом любые уже существующие теги (<blockquote>, <b>, <code>, <a>)
        html_text = markdown_to_telegram_html(text)

        chunks = split_telegram_html(html_text, max_chunk=3800)

        for idx, chunk in enumerate(chunks):
            is_last = (idx == len(chunks) - 1)
            cur_markup = reply_markup if is_last else None
            try:
                await msg.answer(
                    chunk,
                    parse_mode="HTML",
                    reply_markup=cur_markup,
                    link_preview_options=link_preview_options
                )
            except TelegramBadRequest as e:
                logger.warning(f"Telegram HTML parse error in chunk {idx} ({e}), retrying with sanitized plain text...")
                try:
                    # Чистим все теги до чистого текста, чтобы пользователь НИКОГДА не видел сырой HTML код!
                    clean_text = html.unescape(re.sub(r'<[^>]+>', '', chunk))
                    await msg.answer(clean_text, reply_markup=cur_markup)
                except Exception as e2:
                    logger.error(f"Fallback plain text failed ({e2}): {e2}")
            except Exception as e:
                logger.error(f"Failed to send chunk {idx}: {e}")

def build_snowden_menu_text(
    local_status: str = "",
    active_prov: str = "",
    current_model: str = "",
    chat_active: bool = True,
    is_test_mode: bool = False,
    tz_name: str = "",
    next_run: str = "",
    tg_count: int = 0,
    rss_count: int = 0,
    darknet_on: bool = False
) -> str:
    return (
        "🕶 <b>Аналитический агент Сноуден на связи.</b>\n"
        "<i>Автономный мониторинг каналов, глубокая OSINT-разведка и аналитика с ИИ.</i>\n\n"
        "💡 <i>Вы можете писать любой вопрос или задачу прямо сюда — я сам определю нужный инструмент (поиск публикаций в каналах, разведка в Сети и Darknet или аналитическая сводка).</i>"
    )

    # --------------------------------------------------------------------------
    # Главное меню (Команда /start или кнопка "Главное меню", а также слова "старт", "меню")
    # --------------------------------------------------------------------------
    @r.message(Command("start", "menu"))
    @r.message(F.text.lower().in_(["старт", "start", "меню", "menu", "привет", "главное меню", "/start", "/menu"]))
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

        await register_bot_commands(msg.bot)
        await state.clear()
        is_local_on = await llm_router.check_local_health()
        local_status = "🟢 В сети (LM Studio)" if is_local_on else "🔴 Офлайн"

        active_prov = llm_router.active_provider
        current_model = llm_router.get_current_model_for_provider(active_prov if active_prov != "auto" else "openrouter")
        chat_active = storage.is_chat_mode_active()
        is_test_mode = storage.is_test_mode()
        tz_name = scheduler.timezone_name if scheduler else "Europe/Moscow"
        next_run = scheduler.get_next_run_time() if scheduler else "08:50"

        tg_count = len(get_telegram_channels())
        rss_count = len(get_rss_feeds())

        text = build_snowden_menu_text(
            local_status=local_status,
            active_prov=active_prov,
            current_model=current_model,
            chat_active=chat_active,
            is_test_mode=is_test_mode,
            tz_name=tz_name,
            next_run=next_run,
            tg_count=tg_count,
            rss_count=rss_count,
            darknet_on=storage.is_darknet_search_enabled()
        )
        await send_safe_reply(msg, text, reply_markup=get_main_menu_keyboard(chat_active, get_web_app_url()))

    @r.message(Command("help"))
    @r.message(F.text.lower().in_(["/help", "помощь", "справка", "инструкция"]))
    async def cmd_help_msg(msg: Message):
        if not is_admin(msg.from_user.id):
            return
        await register_bot_commands(msg.bot)
        help_text = (
            "📖 <b>Справочник команд терминала «Агент Сноуден»:</b>\n\n"
            "🔍 <b>Основательный поиск, Google AI и Darknet:</b>\n"
            "• <code>/search &lt;запрос&gt;</code> (или <code>/поиск</code>) — глубокий сбор и аналитический отчет ИИ:\n"
            "  1. Ваши собственные каналы и RSS (с цитатами и ссылками).\n"
            "  2. Внешние авторитетные мировые и профильные медиа (Web / Google AI Grounding).\n"
            "  3. Теневой сегмент (.onion / Darknet) — если включен.\n"
            "• <code>/google</code> (или <code>/google on|off</code>) — включить/выключить живой Google AI Search (Gemini Grounding).\n"
            "• <code>/darknet</code> (или <code>/darknet on|off</code>) — включить/выключить поиск в сети Tor (.onion).\n"
            "• <code>/find &lt;запрос&gt;</code> — быстрый поиск конкретного поста в архиве каналов.\n\n"
            "💬 <b>Диалог и оперативная разведка:</b>\n"
            "• <code>/chat</code> — войти в режим прямого диалога со Сноуденом.\n"
            "• <code>/exit</code> — завершить диалог и вернуться к терминалу.\n"
            "• В режиме чата Сноуден опрашивает все каналы (@medvedev_telegram, @ross_name, @Dyadyabatya0, @Voennaja_svodka, @infomil_live, РИА, ТАСС), объединяет факты без дублирования в единый насыщенный абзац, цитирует первоисточники через <b>цитаты</b> и отправляет <b>реальные фото/медиа альбомы</b> с места событий!\n\n"
            "⚡️ <b>Сводки и аналитика:</b>\n"
            "• <code>/digest</code> — немедленно сгенерировать утреннюю аналитическую сводку (СВО, геополитика, полезный софт, безопасность).\n"
            "• <code>/refresh</code> — запустить оперативный опрос всех каналов и обновить базу публикаций прямо сейчас.\n\n"
            "⚙️ <b>Управление источниками и ядром:</b>\n"
            "• <code>/sources</code> — список подключенных каналов и RSS.\n"
            "• <code>/add_channel</code> — добавить Telegram-канал (поддерживаются @юзернейм, ссылки t.me и инвайты t.me/+...).\n"
            "• <code>/add_rss</code> — добавить RSS-ленту.\n"
            "• <code>/del_source</code> — удалить ненужный канал или ленту.\n"
            "• <code>/status</code> — статус оборудования и ядра ИИ.\n"
            "• <code>/test_mode</code> — переключить тестовый режим (вкл/выкл запись в БД).\n"
            "• <code>/clearnews</code> — полностью очистить базу новостей и тестовых дайджестов.\n\n"
            "<i>💡 Для вызова списка команд в любой момент нажимайте кнопку <b>«Меню» ( [/] )</b> в левом нижнем углу чата.</i>"
        )
        await send_safe_reply(msg, help_text, reply_markup=get_main_menu_keyboard(storage.is_chat_mode_active(), get_web_app_url()))

    @r.callback_query(F.data == "btn_menu")
    async def cb_main_menu(call: CallbackQuery, state: FSMContext):
        await call.answer()
        await state.clear()
        is_local_on = await llm_router.check_local_health()
        local_status = "🟢 В сети (LM Studio)" if is_local_on else "🔴 Офлайн"
        active_prov = llm_router.active_provider
        current_model = llm_router.get_current_model_for_provider(active_prov if active_prov != "auto" else "openrouter")
        chat_active = storage.is_chat_mode_active()
        is_test_mode = storage.is_test_mode()
        tz_name = scheduler.timezone_name if scheduler else "Europe/Moscow"
        next_run = scheduler.get_next_run_time() if scheduler else "08:50"

        tg_count = len(get_telegram_channels())
        rss_count = len(get_rss_feeds())

        text = build_snowden_menu_text(
            local_status=local_status,
            active_prov=active_prov,
            current_model=current_model,
            chat_active=chat_active,
            is_test_mode=is_test_mode,
            tz_name=tz_name,
            next_run=next_run,
            tg_count=tg_count,
            rss_count=rss_count,
            darknet_on=storage.is_darknet_search_enabled()
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
            "💬 <b>Диалог с Агентом Сноуденом активен!</b>\n\n"
            f"🧠 <b>Ядро:</b> <code>{active_prov.upper()}</code> (<code>{active_model}</code>)\n\n"
            "• Я знаю актуальную дату (2026 год) и контекст всех свежих публикаций из каналов.\n"
            "• Задавай любые вопросы по СВО, ударам, обстановке, геополитике, коду или полезному софту.\n"
            "• Чтобы выйти из диалога обратно в меню, введи <code>/exit</code> или нажми кнопку ниже."
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

        wait_msg = await msg.answer("⏳ <b>[1/4] Сбор публикаций...</b>\n<i>Опрашиваю Telegram-каналы и RSS-ленты...</i>", parse_mode="HTML")
        animator = LiveProgressAnimator(wait_msg, "<b>[1/4] Сбор публикаций...</b>\n<i>Опрашиваю Telegram-каналы и RSS-ленты...</i>")
        animator.start()

        async def update_progress(text: str):
            animator.update_text(text)

        try:
            res = await digest_builder.generate_digest(on_progress=update_progress)
        finally:
            await animator.stop()
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
    # Очистка базы и архивов: /clearnews или /purge или /clean_db
    # --------------------------------------------------------------------------
    @r.message(Command("clearnews", "purge", "clean_db"))
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
    # Ручной запуск сбора свежих новостей: /refresh или /update или кнопка
    # --------------------------------------------------------------------------
    @r.message(Command("refresh", "update", "collect"))
    @r.callback_query(F.data == "btn_refresh_now")
    async def cmd_refresh_news(event: Message | CallbackQuery):
        user_id = event.from_user.id
        if not is_admin(user_id):
            if isinstance(event, CallbackQuery):
                await event.answer("⛔️ Доступ ограничен", show_alert=True)
            return

        if isinstance(event, CallbackQuery):
            await event.answer("Инициализация оперативного сбора...")
            msg = event.message
        else:
            msg = event

        wait_msg = await msg.answer(
            "⏳ <b>[1/2] Запуск сбора свежих публикаций...</b>\n"
            "<i>Опрашиваю Telegram-каналы и RSS-ленты...</i>",
            parse_mode="HTML"
        )
        animator = LiveProgressAnimator(
            wait_msg,
            "<b>[1/2] Запуск сбора свежих публикаций...</b>\n<i>Опрашиваю Telegram-каналы и RSS-ленты...</i>"
        )
        animator.start()

        try:
            items = await digest_builder.collect_fresh_news()
            animator.update_text(
                "<b>[2/2] Синхронизация оперативного хранилища...</b>\n"
                f"<i>Сохраняю {len(items)} публикаций в буфер поиска...</i>"
            )
            await asyncio.sleep(0.6)
        finally:
            await animator.stop()

        test_status = " <i>(Включен тестовый режим: БД не засоряется)</i>" if storage.is_test_mode() else ""
        try:
            await wait_msg.edit_text(
                f"✅ <b>Сбор публикаций успешно завершен!</b>\n\n"
                f"📥 Собрано свежих материалов: <b>{len(items)}</b>\n"
                f"🗄 Обновлен буфер оперативного поиска для ИИ и диалога в чате.{test_status}\n\n"
                f"💡 Теперь можно сформировать утреннюю сводку через <code>/digest</code> или задать вопрос в <code>/chat</code>.",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка при сборе новостей по /refresh: {e}")
            await wait_msg.edit_text(f"⚠️ Ошибка при сборе публикаций: {e}", parse_mode="HTML")

    # --------------------------------------------------------------------------
    # Основательный многоконтурный поиск: /search <текст> или /поиск <текст>
    # --------------------------------------------------------------------------
    @r.message(Command("search", "поиск"))
    async def cmd_deep_search(msg: Message):
        user_id = msg.from_user.id
        if not is_admin(user_id):
            return

        query = msg.text.partition(" ")[2].strip()
        is_darknet = storage.is_darknet_search_enabled()
        is_google = storage.is_google_ai_search_enabled()

        if not query:
            google_status = "🟢 <b>ВКЛЮЧЕН (Gemini Grounding)</b>" if is_google else "⚪️ <b>ВЫКЛЮЧЕН (DuckDuckGo)</b> (вкл: <code>/google on</code>)"
            dark_status = "🟢 <b>ВКЛЮЧЕН</b>" if is_darknet else "⚪️ <b>ВЫКЛЮЧЕН</b> (вкл: <code>/darknet on</code>)"
            await send_safe_reply(
                msg,
                "🔍 <b>Основательный многоконтурный поиск («Агент Сноуден»):</b>\n\n"
                "Использование:\n"
                "<code>/search &lt;тема, событие, софт, персона или запрос&gt;</code>\n"
                "<i>(или на русском: <code>/поиск &lt;запрос&gt;</code>)</i>\n\n"
                "<b>Сноуден одновременно опрашивает 3 контура разведки:</b>\n"
                "1. 📱 <b>Ваши источники</b> — Telegram-каналы и RSS из вашей базы (с цитатами и ссылками).\n"
                f"2. 🌐 <b>Внешний Web</b> — мировые СМИ, профильные порталы (Google AI: {google_status}).\n"
                f"3. 🧅 <b>Теневой сегмент (.onion / Darknet)</b> — Tor: {dark_status}\n\n"
                "После сбора нейросеть сопоставит все факты, выявит расхождения и подготовит структурированный отчет.\n\n"
                "<b>Примеры:</b>\n"
                "• <code>/search Герань-5 характеристики и боевое применение</code>\n"
                "• <code>/search продвижение в районе Купянска и удары по объектам энергетики</code>\n"
                "• <code>/search критические уязвимости SSH и эксплойты под Linux</code>",
                reply_markup=get_search_settings_keyboard(is_darknet, is_google)
            )
            return

        await msg.bot.send_chat_action(msg.chat.id, "typing")
        badges = []
        if is_google:
            badges.append("🌐 Google AI")
        if is_darknet:
            badges.append("🧅 Darknet")
        badges_str = f" [{', '.join(badges)}]" if badges else ""

        wait_msg = await msg.answer(
            f"🔍 <b>Запущен основательный поиск{badges_str}...</b>\n"
            f"<i>Опрашиваю ваши каналы, внешний веб{' через Google Grounding' if is_google else ''}{' и скрытую сеть Tor' if is_darknet else ''}...</i>",
            parse_mode="HTML"
        )

        from src.pipeline.deep_search import DeepSearchEngine
        deep_engine = DeepSearchEngine()

        async def refresh_func():
            await digest_builder.collect_fresh_news()

        result = await deep_engine.execute_deep_search(
            query=query,
            storage=storage,
            llm_router=llm_router,
            include_darknet=is_darknet,
            refresh_collector_func=refresh_func
        )

        try:
            await wait_msg.delete()
        except Exception:
            pass

        await send_safe_reply(
            msg,
            result["text"],
            reply_markup=get_search_result_keyboard(is_darknet, is_google)
        )

    # --------------------------------------------------------------------------
    # Управление Google AI Search (Gemini Grounding): /google [on/off]
    # --------------------------------------------------------------------------
    @r.message(Command("google", "гугл"))
    async def cmd_google(msg: Message):
        user_id = msg.from_user.id
        if not is_admin(user_id):
            return

        arg = msg.text.partition(" ")[2].strip().lower()
        is_darknet = storage.is_darknet_search_enabled()
        if arg in ["on", "1", "enable", "вкл", "true"]:
            storage.set_google_ai_search(True)
            text = (
                "🌐 <b>Google AI Search (Gemini Grounding) ВКЛЮЧЕН.</b>\n\n"
                "Теперь при основательном поиске (<code>/search &lt;запрос&gt;</code> или запросах в чате) "
                "система использует живой поисковый индекс Google через Gemini Search Grounding. "
                "Это обеспечивает мгновенное нахождение свежих модификаций техники (например, «Герань-5»), "
                "актуальных фронтовых сводок, уязвимостей и официальных источников с автоматическим извлечением прямых ссылок.\n\n"
                "<i>💡 В случае недоступности API-ключа Gemini система автоматически использует резервный веб-шлюз.</i>"
            )
            await send_safe_reply(msg, text, reply_markup=get_search_settings_keyboard(is_darknet, True))
            return
        elif arg in ["off", "0", "disable", "выкл", "false"]:
            storage.set_google_ai_search(False)
            text = (
                "🌐 <b>Google AI Search (Gemini Grounding) ВЫКЛЮЧЕН.</b>\n\n"
                "Внешний веб-поиск переведен в стандартный режим (DuckDuckGo)."
            )
            await send_safe_reply(msg, text, reply_markup=get_search_settings_keyboard(is_darknet, False))
            return

        current_state = storage.is_google_ai_search_enabled()
        status_label = "🟢 <b>ВКЛЮЧЕН (Gemini Grounding)</b>" if current_state else "⚪️ <b>ВЫКЛЮЧЕН (DuckDuckGo)</b>"
        text = (
            f"🌐 <b>Режим внешнего веб-поиска (Google AI Grounding):</b>\n\n"
            f"Текущий статус: {status_label}\n\n"
            f"При включении Сноуден задействует прямой поиск Google через Gemini AI Grounding, "
            f"обеспечивая поиск новейших модификаций (например, «Герань-5», редкие типы БПЛА и РЭБ, свежие патчи ПО) "
            f"с валидацией ссылок через реестр доверенных источников.\n\n"
            f"• Включить: <code>/google on</code>\n"
            f"• Выключить: <code>/google off</code>\n\n"
            f"Или переключите кнопкой ниже:"
        )
        await send_safe_reply(msg, text, reply_markup=get_search_settings_keyboard(is_darknet, current_state))

    @r.callback_query(F.data.in_(["btn_google_on", "btn_google_off"]))
    async def cb_google_toggle(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            await call.answer("⛔️ Доступ ограничен", show_alert=True)
            return

        enable = (call.data == "btn_google_on")
        storage.set_google_ai_search(enable)
        await call.answer(f"Google AI Search: {'ВКЛЮЧЕН' if enable else 'ВЫКЛЮЧЕН'}", show_alert=True)

        is_darknet = storage.is_darknet_search_enabled()
        status_label = "🟢 <b>ВКЛЮЧЕН</b>" if enable else "⚪️ <b>ВЫКЛЮЧЕН</b>"
        text = (
            f"🌐 <b>Режим внешнего веб-поиска (Google AI Grounding):</b>\n\n"
            f"Текущий статус: {status_label}\n\n"
            f"При включении Сноуден опрашивает живой Google Search через Gemini Grounding с разметкой источников.\n\n"
            f"• Включить: <code>/google on</code>\n"
            f"• Выключить: <code>/google off</code>"
        )
        await safe_edit_text(call, text, get_search_settings_keyboard(is_darknet, enable))

    # --------------------------------------------------------------------------
    # Управление поиском в Darknet (.onion): /darknet [on/off]
    # --------------------------------------------------------------------------
    @r.message(Command("darknet", "onion", "даркнет"))
    async def cmd_darknet(msg: Message):
        user_id = msg.from_user.id
        if not is_admin(user_id):
            return

        arg = msg.text.partition(" ")[2].strip().lower()
        is_google = storage.is_google_ai_search_enabled()
        if arg in ["on", "1", "enable", "вкл", "true"]:
            storage.set_darknet_search(True)
            text = (
                "🧅 <b>Поиск в Darknet (.onion) ВКЛЮЧЕН.</b>\n\n"
                "Теперь при выполнении основательного поиска (<code>/search &lt;запрос&gt;</code> или запроса в чате) "
                "система будет дополнительно опрашивать поисковые индексы теневого сегмента сети Tor (скрытые сервисы .onion, теневые форумы, дампы).\n\n"
                "<i>💡 Запросы отправляются в безопасном режиме (без исполнения браузерных JS-скриптов).</i>"
            )
            await send_safe_reply(msg, text, reply_markup=get_search_settings_keyboard(True, is_google))
            return
        elif arg in ["off", "0", "disable", "выкл", "false"]:
            storage.set_darknet_search(False)
            text = (
                "🧅 <b>Поиск в Darknet (.onion) ВЫКЛЮЧЕН.</b>\n\n"
                "Теперь поиск проводится только по вашим каналам и внешнему открытому вебу."
            )
            await send_safe_reply(msg, text, reply_markup=get_search_settings_keyboard(False, is_google))
            return

        current_state = storage.is_darknet_search_enabled()
        status_label = "🟢 <b>ВКЛЮЧЕН</b>" if current_state else "⚪️ <b>ВЫКЛЮЧЕН</b>"
        text = (
            f"🧅 <b>Мониторинг и поиск в теневом сегменте (.onion / Darknet):</b>\n\n"
            f"Текущий статус: {status_label}\n\n"
            f"При включении Сноуден опрашивает индексаторы сети Tor на предмет упоминаний, "
            f"утечек, эксплойтов и публикаций в скрытых сервисах.\n\n"
            f"• Включить: <code>/darknet on</code>\n"
            f"• Выключить: <code>/darknet off</code>\n\n"
            f"Или переключите кнопкой ниже:"
        )
        await send_safe_reply(msg, text, reply_markup=get_search_settings_keyboard(current_state, is_google))

    @r.callback_query(F.data.in_(["btn_darknet_on", "btn_darknet_off"]))
    async def cb_darknet_toggle(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            await call.answer("⛔️ Доступ ограничен", show_alert=True)
            return

        enable = (call.data == "btn_darknet_on")
        storage.set_darknet_search(enable)
        await call.answer(f"Поиск в Darknet: {'ВКЛЮЧЕН' if enable else 'ВЫКЛЮЧЕН'}", show_alert=True)

        is_google = storage.is_google_ai_search_enabled()
        status_label = "🟢 <b>ВКЛЮЧЕН</b>" if enable else "⚪️ <b>ВЫКЛЮЧЕН</b>"
        text = (
            f"🧅 <b>Мониторинг и поиск в теневом сегменте (.onion / Darknet):</b>\n\n"
            f"Текущий статус: {status_label}\n\n"
            f"При включении Сноуден опрашивает индексаторы сети Tor на предмет упоминаний, "
            f"утечек, эксплойтов и публикаций в скрытых сервисах.\n\n"
            f"• Включить: <code>/darknet on</code>\n"
            f"• Выключить: <code>/darknet off</code>"
        )
        await safe_edit_text(call, text, get_search_settings_keyboard(enable, is_google))

    @r.callback_query(F.data == "btn_search_menu")
    async def cb_search_menu(call: CallbackQuery):
        is_darknet = storage.is_darknet_search_enabled()
        is_google = storage.is_google_ai_search_enabled()
        dark_status = "🟢 <b>ВКЛЮЧЕН</b>" if is_darknet else "⚪️ <b>ВЫКЛЮЧЕН</b>"
        google_status = "🟢 <b>ВКЛЮЧЕН</b>" if is_google else "⚪️ <b>ВЫКЛЮЧЕН</b>"
        text = (
            "🔍 <b>Центр поиска и OSINT-разведки («Агент Сноуден»):</b>\n\n"
            "<b>Сноуден одновременно опрашивает 3 контура:</b>\n"
            "1. 📱 <b>Ваши источники</b> — подключенные Telegram-каналы и RSS.\n"
            f"2. 🌐 <b>Внешний Web</b> (Google AI Grounding: {google_status}) — мировые СМИ, профильные порталы, базы эксплойтов.\n"
            f"3. 🧅 <b>Теневой сегмент (.onion / Darknet)</b> — {dark_status}\n\n"
            "<b>Как пользоваться:</b>\n"
            "• Просто напишите в чат любой вопрос или тему (ИИ сам выполнит поиск и подготовит отчет).\n"
            "• Либо используйте команды <code>/search &lt;тема&gt;</code> или <code>/find &lt;пост&gt;</code>."
        )
        await safe_edit_text(call, text, get_search_settings_keyboard(is_darknet, is_google))

    # --------------------------------------------------------------------------
    # Быстрый поиск постов в архиве каналов: /find <текст>
    # --------------------------------------------------------------------------
    @r.message(Command("find"))
    async def cmd_find_post(msg: Message):
        user_id = msg.from_user.id
        if not is_admin(user_id):
            return

        query = msg.text.partition(" ")[2].strip()
        if not query:
            await send_safe_reply(
                msg,
                "🔎 <b>Быстрый поиск постов в Telegram-каналах:</b>\n\n"
                "Использование:\n"
                "<code>/find &lt;описание поста, ключевые слова, тема&gt;</code>\n\n"
                "<i>Для глубокого многоконтурного поиска (каналы + Web + Darknet) используйте команду <code>/search</code>.</i>"
            )
            return

        await msg.bot.send_chat_action(msg.chat.id, "typing")

        from src.pipeline.semantic_search import SemanticSearchEngine
        search_engine = SemanticSearchEngine()
        clean_q, target_channels = search_engine.extract_search_intent(query)

        all_candidates = storage.get_news_feed(limit=500)

        # Если база пуста или в ней мало постов (например, только что очистили) — оперативно собираем публикации со всех каналов!
        if len(all_candidates) < 20:
            try:
                await msg.bot.send_chat_action(msg.chat.id, "typing")
                await digest_builder.collect_fresh_news()
                all_candidates = storage.get_news_feed(limit=500)
            except Exception as e:
                logger.warning(f"Оперативный сбор каналов для /find: {e}")

        results = await search_engine.search_posts(clean_q, all_candidates, limit=5, target_channels=target_channels)

        # Если ничего не найдено и кандидатов было мало — подтягиваем свежие посты и повторяем поиск
        if not results and len(all_candidates) < 60:
            try:
                await msg.bot.send_chat_action(msg.chat.id, "typing")
                await digest_builder.collect_fresh_news()
                all_candidates = storage.get_news_feed(limit=500)
                results = await search_engine.search_posts(clean_q, all_candidates, limit=5, target_channels=target_channels)
            except Exception as e:
                logger.warning(f"Повторный сбор новостей для /find: {e}")

        if not results:
            # Если в свежем буфере каналов поста нет (например, он вышел несколько дней или недель назад) —
            # ищем публикацию в поисковом индексе архивов Telegram через DuckDuckGo!
            await msg.bot.send_chat_action(msg.chat.id, "typing")
            from src.pipeline.deep_search import DeepSearchEngine
            deep_engine = DeepSearchEngine()

            web_q = ""
            if target_channels:
                ch_filter = " OR ".join([f"site:t.me/{c}" for c in target_channels])
                web_q = f"({ch_filter}) {clean_q[:140]}"
            else:
                web_q = f"site:t.me {clean_q[:140]}"

            web_results = await deep_engine.search_web(web_q, limit=5)
            if not web_results:
                # Если строгий фильтр site:t.me вернул пустоту — ищем с ключевым словом telegram
                web_results = await deep_engine.search_web(f"telegram {clean_q[:140]}", limit=5)

            if web_results:
                out_lines = [f"🎯 <b>Найдено в архиве Telegram по запросу:</b> <i>«{html.escape(clean_q)}»</i>\n"]
                for i, it in enumerate(web_results, 1):
                    tit = it.get("title", "")
                    url = it.get("url", "")
                    snip = (it.get("snippet", "") or "")[:280].strip()
                    badge = it.get("trust_badge", "📱 [Архив Telegram]")
                    out_lines.append(
                        f"<b>{i}. {badge} {html.escape(tit)}</b>\n"
                        f"<blockquote>{html.escape(snip)}...</blockquote>\n"
                        f"🔗 <a href=\"{url}\">Перейти к публикации</a>\n"
                    )
                await send_safe_reply(msg, "\n".join(out_lines))
                return

            await send_safe_reply(msg, f"🔍 По запросу <i>«{html.escape(clean_q)}»</i> ничего не найдено в архивах каналов.\nПопробуйте <code>/search {html.escape(clean_q)}</code> для глубокого поиска во внешнем Web и сети Tor.")
            return

        out_lines = [f"🎯 <b>Найдено в каналах по запросу:</b> <i>«{html.escape(clean_q)}»</i>\n"]
        for i, it in enumerate(results, 1):
            ch = it.get("channel", "Канал")
            pub = (it.get("published_at") or "")[:10]
            url = it.get("url", "")
            content = (it.get("content", "") or "")[:280].strip()
            out_lines.append(
                f"<b>{i}. {ch}</b> {f'({pub})' if pub else ''}\n"
                f"<blockquote>{html.escape(content)}...</blockquote>\n"
                f"🔗 <a href=\"{url}\">Перейти к публикации</a>\n"
            )

        await send_safe_reply(msg, "\n".join(out_lines))

    # --------------------------------------------------------------------------
    # Тумблер тестового режима (не сохранять в БД во время тестов): /test_mode или /testmode
    # --------------------------------------------------------------------------
    @r.message(Command("test_mode", "testmode"))
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
            "<i>Новости НЕ помечаются прочитанными и НЕ сжигаются в постоянной БД. "
            "Дайджесты и чат не засоряют диск. Можно тестировать бесконечно!</i>"
        ) if now_mode else (
            "⚪️ <b>ВЫКЛЮЧЕН (0 / False)</b>\n"
            "<i>Боевой режим: новости помечаются как прочитанные и архивируются в постоянную БД.</i>"
        )
        await send_safe_reply(
            msg,
            f"⚙️ <b>Тумблер тестового режима:</b>\n\n{status_text}\n\n"
            f"Команды: <code>/test_mode on</code> или <code>/test_mode off</code>"
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
        if not clean_user or not re.match(r'^\+?[a-zA-Z0-9_-]{3,45}$', clean_user):
            await send_safe_reply(
                msg,
                "❌ Некорректный юзернейм или ссылка канала.\n"
                "Поддерживаются: <code>@channel</code>, <code>https://t.me/channel</code>, а также инвайт-ссылки <code>https://t.me/+...</code>.\n"
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
    @r.message(Command("add_channel", "addchannel"))
    async def cmd_quick_addchannel(msg: Message):
        if not is_admin(msg.from_user.id):
            return

        parts = msg.text.strip().split(maxsplit=2)
        if len(parts) < 2:
            await send_safe_reply(
                msg,
                "ℹ️ <b>Использование команды:</b>\n"
                "<code>/add_channel @username [Категория]</code>\n\n"
                "<i>Примеры:</i>\n"
                "• <code>/add_channel @xakep_ru</code> (ИИ автоматически определит категорию!)\n"
                "• <code>/add_channel @habr_com DevOps & Linux</code>\n"
                "• <code>/add_channel https://t.me/ai_newz</code>"
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

    @r.message(Command("del_channel", "delchannel", "del_source", "delsource"))
    async def cmd_quick_delchannel(msg: Message):
        if not is_admin(msg.from_user.id):
            return

        parts = msg.text.strip().split()
        if len(parts) < 2:
            await send_safe_reply(msg, "ℹ️ <b>Использование:</b> <code>/del_channel @username</code>")
            return

        username = parts[1]
        success, result_msg = remove_telegram_channel(username)
        await send_safe_reply(msg, f"{'🗑' if success else '⚠️'} {result_msg}", reply_markup=get_sources_menu_keyboard())

    @r.message(Command("add_rss", "addrss"))
    async def cmd_quick_addrss(msg: Message):
        if not is_admin(msg.from_user.id):
            return

        parts = msg.text.strip().split(maxsplit=2)
        if len(parts) < 2:
            await send_safe_reply(
                msg,
                "ℹ️ <b>Использование команды:</b>\n"
                "<code>/add_rss &lt;URL&gt; [Категория]</code>\n\n"
                "<i>Примеры:</i>\n"
                "• <code>/add_rss https://3dnews.ru/news/rss/</code> (ИИ определит категорию автоматически)\n"
                "• <code>/add_rss https://habr.com/ru/rss/hubs/all/ Hardware</code>"
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

    @r.message(Command("del_rss", "delrss"))
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
        darknet_on = storage.is_darknet_search_enabled()
        google_ai_on = storage.is_google_ai_search_enabled()

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
            f"4. 🌐 *Контуры поиска и разведки:*\n"
            f"   - Google AI Search (Grounding): {'🟢 ВКЛЮЧЕН (/google off)' if google_ai_on else '⚪️ ВЫКЛЮЧЕН (/google on)'}\n"
            f"   - Сеть Tor (Darknet .onion): {'🟢 ВКЛЮЧЕН (/darknet off)' if darknet_on else '⚪️ ВЫКЛЮЧЕН (/darknet on)'}\n\n"
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

        await msg.bot.send_chat_action(msg.chat.id, "typing")
        user_query = msg.text

        # Извлекаем контекст: история, последний дайджест, свежие новости
        history = storage.get_chat_history(limit=6)
        latest_digest = storage.get_latest_digest()

        # ----------------------------------------------------------------------
        # АВТОНОМНЫЙ ИИ-МАРШРУТИЗАТОР НАМЕРЕНИЙ (AI Intent Router)
        # ИИ сам определяет, какую команду применить: find_post, deep_search, digest, status, chat
        # ----------------------------------------------------------------------
        from src.pipeline.ai_intent_router import AIIntentRouter
        intent_router = AIIntentRouter()
        decision = await intent_router.classify(user_query, llm_router=llm_router)
        action = decision.get("action", "chat")
        clean_q = decision.get("clean_query") or user_query
        target_channels = decision.get("target_channels") or []
        include_darknet = decision.get("include_darknet", False)
        logger.info(f"🧠 AI Intent Router: action='{action}', query='{clean_q[:40]}', darknet={include_darknet}")

        # 1. ДЕЙСТВИЕ: find_post (поиск конкретного поста / видео / цитаты в каналах или архивах)
        if action == "find_post":
            all_candidates = storage.get_news_feed(limit=500)
            if len(all_candidates) < 20:
                try:
                    await digest_builder.collect_fresh_news()
                    all_candidates = storage.get_news_feed(limit=500)
                except Exception as e:
                    logger.warning(f"Сбор каналов для AI find_post: {e}")

            from src.pipeline.semantic_search import SemanticSearchEngine
            search_engine = SemanticSearchEngine()
            results = await search_engine.search_posts(clean_q, all_candidates, limit=5, target_channels=target_channels)

            # Если в локальном суточном буфере нет — ищем в архиве Telegram через DuckDuckGo!
            if not results:
                from src.pipeline.deep_search import DeepSearchEngine
                deep_engine = DeepSearchEngine()
                web_q = f"({' OR '.join([f'site:t.me/{c}' for c in target_channels])}) {clean_q[:140]}" if target_channels else f"site:t.me {clean_q[:140]}"
                web_results = await deep_engine.search_web(web_q, limit=4)
                if not web_results:
                    web_results = await deep_engine.search_web(f"telegram {clean_q[:140]}", limit=4)
                if web_results:
                    results = [{
                        "channel": w.get("trust_badge", "📱 [Архив Telegram]"),
                        "title": w.get("title", ""),
                        "content": w.get("snippet", ""),
                        "url": w.get("url", ""),
                        "published_at": ""
                    } for w in web_results]

            if results:
                out_lines = [f"🎯 <b>Найдено в каналах по запросу:</b> <i>«{html.escape(clean_q)}»</i>\n"]
                for i, it in enumerate(results, 1):
                    ch = it.get("channel", "Канал")
                    pub = (it.get("published_at") or "")[:10]
                    url = it.get("url", "")
                    content = (it.get("content", "") or "")[:280].strip()
                    out_lines.append(
                        f"<b>{i}. {ch}</b> {f'({pub})' if pub else ''}\n"
                        f"<blockquote>{html.escape(content)}...</blockquote>\n"
                        f"🔗 <a href=\"{url}\">Перейти к публикации</a>\n"
                    )
                await send_safe_reply(msg, "\n".join(out_lines), reply_markup=get_chat_control_keyboard())
                return
            else:
                await send_safe_reply(
                    msg,
                    f"🔍 По запросу <i>«{html.escape(clean_q)}»</i> в архивах каналов публикаций не найдено.\n"
                    f"Попробуйте <code>/search {html.escape(clean_q)}</code> для поиска по открытому интернету.",
                    reply_markup=get_chat_control_keyboard()
                )
                return

        # 2. ДЕЙСТВИЕ: deep_search (глубокий поиск фактов, внешний веб, OSINT, утечки, Tor)
        if action == "deep_search":
            is_darknet = include_darknet or storage.is_darknet_search_enabled()
            is_google = storage.is_google_ai_search_enabled()
            from src.pipeline.deep_search import DeepSearchEngine
            deep_engine = DeepSearchEngine()

            async def refresh_func():
                await digest_builder.collect_fresh_news()

            result = await deep_engine.execute_deep_search(
                query=clean_q,
                storage=storage,
                llm_router=llm_router,
                include_darknet=is_darknet,
                refresh_collector_func=refresh_func
            )
            await send_safe_reply(msg, result["text"], reply_markup=get_search_result_keyboard(is_darknet, is_google))
            return

        # 3. ДЕЙСТВИЕ: digest (генерация сводки новостей по запросу)
        if action == "digest":
            status_msg = await msg.answer("📰 <b>Готовлю персональную сводку по вашему запросу...</b>", parse_mode="HTML")
            try:
                res = await digest_builder.build_digest(custom_query=clean_q or None)
                try:
                    await status_msg.delete()
                except Exception:
                    pass
                if res.get("success"):
                    formatted_html = format_digest_to_collapsible_html(res.get("text", ""))
                    await send_safe_reply(msg, formatted_html, reply_markup=get_main_menu_keyboard(chat_mode=True, web_app_url=get_web_app_url()))
                else:
                    await send_safe_reply(msg, f"⚠️ Не удалось сформировать сводку: {res.get('text')}")
            except Exception as e:
                logger.error(f"Ошибка дайджеста через AI Router: {e}")
                await send_safe_reply(msg, f"⚠️ Ошибка при формировании сводки: {e}")
            return

        # 4. ДЕЙСТВИЕ: status (диагностика оборудования и доступности узлов)
        if action == "status":
            local_ok = await llm_router.check_local_health()
            local_status = "🟢 Онлайн" if local_ok else "🔴 Офлайн"
            active_prov = llm_router.active_provider
            cur_model = llm_router.get_current_model_for_provider(active_prov)
            dark_on = storage.is_darknet_search_enabled()
            status_text = (
                "🖥 <b>Диагностика и статус узлов «Сноуден»:</b>\n\n"
                f"• <b>Ноутбук-сервер 24/7:</b> 🟢 В строю (Linux Mint / Docker host)\n"
                f"• <b>Вычислительный ПК (LAN):</b> {local_status} (LM Studio)\n"
                f"• <b>Активное ядро ИИ:</b> <code>{active_prov.upper()}</code> • <code>{cur_model}</code>\n"
                f"• <b>Darknet (.onion):</b> {'🟢 Включен' if dark_on else '⚪️ Выключен'}\n"
                f"• <b>База публикаций:</b> {storage.count_news_feed()} записей в памяти"
            )
            await send_safe_reply(msg, status_text, reply_markup=get_chat_control_keyboard())
            return

        # 5. ДЕЙСТВИЕ: chat (диалог с автоматическим веб-поиском при нехватке локальных данных)
        q_lower = user_query.lower()
        is_news_inquiry = any(w in q_lower for w in [
            "новост", "украин", "сво", "фронт", "трамп", "войн", "политик",
            "росс", "дядя батя", "рыбар", "сводк", "ситуаци", "что произошло", "что слышно",
            "атак", "удар", "дрон", "бпла", "взрыв", "прилет", "пожар", "нефт", "мост", "софт",
            "программ", "github", "хакер", "безопасн", "подробн", "инфа", "инфу", "детал", "обстановк"
        ])

        # 1. Поиск публикаций по семантике и корням в локальной базе (до 60 кандидатов)
        found_items = storage.search_news_feed(user_query, limit=60)

        # 2. Если результатов мало или запрос информационный — оперативно опрашиваем каналы
        if len(found_items) < 10 or is_news_inquiry:
            try:
                await digest_builder.collect_fresh_news()
                found_items = storage.search_news_feed(user_query, limit=60)
            except Exception as e:
                logger.warning(f"Оперативный сбор новостей для чата: {e}")

        # Объединяем с общей свежей лентой для максимальной полноты картины
        general_feed = storage.get_news_feed(limit=80)
        seen_hashes = {it.get("item_hash") or it.get("url") for it in found_items}
        candidate_items = list(found_items)
        for it in general_feed:
            h = it.get("item_hash") or it.get("url")
            if h not in seen_hashes:
                candidate_items.append(it)
                seen_hashes.add(h)

        # 3. Детальное ранжирование релевантности публикации запросу
        q_stems = extract_stems(user_query)

        # Отбираем публикации, в которых РЕАЛЬНО встречаются стеммы запроса
        matching_local_items = []
        for it in candidate_items:
            corpus = f"{it.get('title', '')} {it.get('content', '')}".lower()
            matched = sum(1 for s in q_stems if s in corpus)
            if matched >= 1:
                matching_local_items.append((matched, it))

        matching_local_items.sort(key=lambda x: x[0], reverse=True)
        relevant_local_items = [it for _, it in matching_local_items]

        # Если в локальных каналах новостей по запросу мало (< 2) или 0 совпадений:
        # Автоматически запускаем внешний Web-поиск (Perplexity/OSINT режим)!
        web_sources_str = ""
        if len(relevant_local_items) < 2:
            try:
                await msg.bot.send_chat_action(msg.chat.id, "typing")
                from src.pipeline.deep_search import DeepSearchEngine
                deep_engine = DeepSearchEngine()
                from src.pipeline.semantic_search import SemanticSearchEngine
                clean_q, _ = SemanticSearchEngine.extract_search_intent(user_query)
                web_findings = await deep_engine.search_web(clean_q, limit=5)
                if web_findings:
                    w_lines = []
                    for i, w in enumerate(web_findings, 1):
                        badge = w.get("trust_badge", "🌐")
                        w_lines.append(
                            f"[{i}] {badge} Заголовок: {w.get('title')}\n"
                            f"Выдержка: {w.get('snippet')}\n"
                            f"Ссылка: {w.get('url')}"
                        )
                    web_sources_str = "\n\n".join(w_lines)
            except Exception as web_err:
                logger.warning(f"Chat web search fallback error: {web_err}")

        # 4. Извлечение реальных фотографий: фото прикрепляем ТОЛЬКО если есть реальные тематические посты
        relevant_photos = []
        if relevant_local_items:
            top_candidates = relevant_local_items[:4]
            for it in top_candidates:
                for img in it.get("media_urls", []):
                    if img and img.startswith("http") and img not in relevant_photos:
                        relevant_photos.append(img)
                img0 = it.get("image_url")
                if img0 and img0.startswith("http") and img0 not in relevant_photos:
                    relevant_photos.append(img0)

        # 5. Формирование максимально полного пакета фактов для LLM
        recent_news_str = ""
        news_items_to_send = relevant_local_items if relevant_local_items else candidate_items[:15]
        if news_items_to_send and (relevant_local_items or not web_sources_str):
            news_parts = []
            for i, it in enumerate(news_items_to_send[:25], 1):
                ch = it.get("channel", "Канал")
                cat = it.get("category", "Новости")
                t = it.get("title", "")
                c = (it.get("content", "") or "").strip()[:1800]
                u = it.get("url", "")
                pub = it.get("published_at") or it.get("created_at") or ""
                has_media = "Да" if it.get("image_url") or it.get("media_urls") else "Нет"
                news_parts.append(
                    f"[{i}] Источник: {ch} ({cat}) | Время: {pub} | Медиа: {has_media}\n"
                    f"Заголовок: {t}\n"
                    f"Текст публикации: {c}\n"
                    f"Ссылка: {u}"
                )
            recent_news_str = "\n---\n".join(news_parts)

        try:
            res = await llm_router.generate_response(
                task="chat",
                user_prompt=user_query,
                chat_history=history,
                latest_digest=latest_digest,
                recent_news=recent_news_str,
                web_sources=web_sources_str
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

            # 6. Отправка ответа пользователю с настоящими фотографиями (если есть)
            photos_to_send = relevant_photos[:4]
            sent_with_media = False

            if photos_to_send:
                try:
                    downloaded_media = await download_images_as_media_group(photos_to_send, max_count=4)
                    if len(downloaded_media) == 1:
                        caption_html = markdown_to_telegram_html(answer_text + footer)
                        if len(caption_html) <= 1024:
                            await msg.answer_photo(
                                photo=downloaded_media[0].media,
                                caption=caption_html,
                                parse_mode="HTML",
                                reply_markup=get_chat_control_keyboard()
                            )
                            sent_with_media = True
                        else:
                            await msg.answer_photo(photo=downloaded_media[0].media)
                            await send_safe_reply(msg, answer_text + footer, reply_markup=get_chat_control_keyboard())
                            sent_with_media = True
                    elif len(downloaded_media) > 1:
                        await msg.answer_media_group(media=downloaded_media)
                        await send_safe_reply(msg, answer_text + footer, reply_markup=get_chat_control_keyboard())
                        sent_with_media = True
                except Exception as media_err:
                    logger.warning(f"Ошибка при отправке скачанных медиафайлов: {media_err}")

            if not sent_with_media:
                await send_safe_reply(msg, answer_text + footer, reply_markup=get_chat_control_keyboard())

        except Exception as e:
            logger.exception(f"Unhandled error in chat handler: {e}")
            await send_safe_reply(msg, f"⚠️ Внутренняя ошибка обработчика: {e}")

    return r
