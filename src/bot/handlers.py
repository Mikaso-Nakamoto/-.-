import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, MessageReactionUpdated
from aiogram.exceptions import TelegramBadRequest

from src.llm.router import LLMRouter
from src.llm.prompts import clean_telegram_markdown
from src.pipeline.digest_builder import DigestBuilder
from src.pipeline.storage import Storage
from src.bot.keyboards import (
    get_main_menu_keyboard,
    get_chat_control_keyboard,
    get_digest_feedback_keyboard,
    get_provider_selection_keyboard,
    get_models_keyboard,
    get_provider_browser_keyboard
)
from src.config import load_sources

logger = logging.getLogger(__name__)

def setup_router(digest_builder: DigestBuilder, llm_router: LLMRouter, storage: Storage, admin_id: int, scheduler=None) -> Router:
    r = Router()

    def is_admin(user_id: int) -> bool:
        return admin_id == 0 or user_id == admin_id

    async def safe_edit_text(call: CallbackQuery, text: str, reply_markup=None):
        try:
            await call.message.edit_text(clean_telegram_markdown(text), parse_mode="Markdown", reply_markup=reply_markup)
        except TelegramBadRequest as e:
            if "message is not modified" in str(e).lower():
                pass
            else:
                logger.warning(f"Ошибка редактирования сообщения: {e}")
        except Exception as e:
            logger.warning(f"Ошибка при edit_text: {e}")

    # --------------------------------------------------------------------------
    # Главное меню (Команда /start или кнопка "Главное меню")
    # --------------------------------------------------------------------------
    @r.message(Command("start"))
    async def cmd_start_msg(msg: Message):
        if not is_admin(msg.from_user.id):
            await msg.answer("⛔️ Доступ ограничен.")
            return

        is_local_on = await llm_router.check_local_health()
        local_status = "🟢 В сети (LM Studio)" if is_local_on else "🔴 Офлайн (Включен облачный резерв)"

        active_prov = llm_router.active_provider
        current_model = llm_router.get_current_model_for_provider(active_prov if active_prov != "auto" else "openrouter")
        chat_active = storage.is_chat_mode_active()
        tz_name = scheduler.timezone_name if scheduler else "Europe/Moscow"
        next_run = scheduler.get_next_run_time() if scheduler else "08:50"

        text = (
            f"👋 *Привет! Я твой автономный ИИ-хаб.*\n\n"
            f"💻 *Ноутбук-сервер:* 🟢 24/7 Активен\n"
            f"🖥 *Основной ПК (LM Studio):* {local_status}\n"
            f"🧠 *Активный провайдер:* `{active_prov.upper()}`\n"
            f"🎯 *Текущая модель:* `{current_model}`\n"
            f"⏰ *Расписание:* `08:50 ({tz_name})`\n"
            f"⏳ *Следующая сводка:* `{next_run}`\n"
            f"💬 *Режим чата:* {'🟢 ВКЛЮЧЕН' if chat_active else '⚪️ Выключен'}\n\n"
            f"Используйте кнопки ниже для управления:"
        )
        await msg.answer(clean_telegram_markdown(text), parse_mode="Markdown", reply_markup=get_main_menu_keyboard(chat_active))
        await msg.answer(clean_telegram_markdown(text), parse_mode="Markdown", reply_markup=get_main_menu_keyboard(chat_active))

    @r.callback_query(F.data == "btn_menu")
    async def cb_main_menu(call: CallbackQuery):
        await call.answer()
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
        await safe_edit_text(call, text, get_main_menu_keyboard(chat_active))

    # --------------------------------------------------------------------------
    # Управление режимом диалога (Включение / Выход / Очистка контекста)
    # --------------------------------------------------------------------------
    @r.message(Command("chat"))
    @r.callback_query(F.data == "btn_chat_start")
    async def cmd_chat_start(event: Message | CallbackQuery):
        storage.set_chat_mode(True)
        active_prov = llm_router.active_provider
        active_model = llm_router.get_current_model_for_provider(active_prov if active_prov != "auto" else "openrouter")

        text = (
            f"💬 *Режим диалога с ИИ активирован!*\n\n"
            f"Нейросеть: *{active_prov.upper()}* (`{active_model}`).\n\n"
            f"• Модель знает текущую дату, время и содержание последнего дайджеста новостей.\n"
            f"• Просто пишите сообщения сюда. Чтобы завершить диалог, нажмите кнопку ниже или введите /exit."
        )

        if isinstance(event, CallbackQuery):
            await event.answer("Режим диалога включен")
            await safe_edit_text(event, text, get_chat_control_keyboard())
        else:
            await event.answer(clean_telegram_markdown(text), parse_mode="Markdown", reply_markup=get_chat_control_keyboard())

    @r.message(Command("exit"))
    @r.callback_query(F.data == "btn_chat_stop")
    async def cmd_chat_stop(event: Message | CallbackQuery):
        storage.set_chat_mode(False)
        text = (
            f"🔴 *Режим диалога завершен.*\n\n"
            f"Вы вернулись в стандартный режим. Сообщения не будут отправляться в LLM, пока вы снова не включите чат."
        )
        if isinstance(event, CallbackQuery):
            await event.answer("Режим диалога отключен")
            await safe_edit_text(event, text, get_main_menu_keyboard(chat_mode=False))
        else:
            await event.answer(clean_telegram_markdown(text), parse_mode="Markdown", reply_markup=get_main_menu_keyboard(chat_mode=False))

    @r.callback_query(F.data == "btn_chat_clear")
    async def cb_chat_clear(call: CallbackQuery):
        storage.clear_chat_history()
        await call.answer("🧹 Память диалога очищена!", show_alert=True)

    # --------------------------------------------------------------------------
    # Ручной запуск дайджеста (Создает новое сообщение со сводкой)
    # --------------------------------------------------------------------------
    @r.message(Command("digest"))
    @r.callback_query(F.data == "btn_run_digest")
    async def cmd_digest(event: Message | CallbackQuery):
        msg = event if isinstance(event, Message) else event.message
        if isinstance(event, CallbackQuery):
            await event.answer("Сбор публикаций и запуск ИИ...")

        wait_msg = await msg.answer("⏳ *Идет сбор свежих новостей и структурирование через ИИ...*", parse_mode="Markdown")

        res = await digest_builder.generate_digest()
        await wait_msg.delete()

        # Очищаем Markdown от возможных решеток ###
        text = clean_telegram_markdown(res["text"])

        # Отправляем дайджест с кнопками оценки (👍 / 👎)
        if len(text) > 4000:
            for x in range(0, len(text), 4000):
                await msg.answer(text[x:x+4000], parse_mode="Markdown")
            await msg.answer("👆 *Оцените подборку для обучения рекомендаций:*", parse_mode="Markdown", reply_markup=get_digest_feedback_keyboard())
        else:
            await msg.answer(text, parse_mode="Markdown", reply_markup=get_digest_feedback_keyboard())

    # --------------------------------------------------------------------------
    # Оценка дайджеста (Обратная связь и обучение предпочтений)
    # --------------------------------------------------------------------------
    @r.callback_query(F.data.in_(["fb_like", "fb_dislike"]))
    async def on_digest_feedback(call: CallbackQuery):
        is_like = call.data == "fb_like"
        context_snippet = call.message.text[:250] if call.message.text else "Дайджест"
        storage.record_feedback("like" if is_like else "dislike", context_snippet)

        alert_msg = "👍 Зафиксировано! Ваши предпочтения сохранены в user_learned_preferences.txt и учтутся завтра." if is_like else "👎 Учтено! Снизим приоритет подобных тем в будущих сводках."
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
    # Выбор провайдеров и моделей (РЕШЕНИЕ ПРОБЛЕМЫ: редактирование на месте!)
    # --------------------------------------------------------------------------
    @r.message(Command("model"))
    @r.callback_query(F.data == "btn_select_provider")
    async def cmd_model(event: Message | CallbackQuery):
        kb = get_provider_selection_keyboard(llm_router.active_provider)
        text = "🧠 *Выберите активного ИИ-провайдера:*"

        if isinstance(event, CallbackQuery):
            await event.answer()
            await safe_edit_text(event, text, kb)
        else:
            await event.answer(clean_telegram_markdown(text), parse_mode="Markdown", reply_markup=kb)

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
    # Статус системы и источники (Редактирование на месте)
    # --------------------------------------------------------------------------
    @r.message(Command("status"))
    @r.callback_query(F.data == "btn_status")
    async def cmd_status(event: Message | CallbackQuery):
        local_on = await llm_router.check_local_health()
        tz_name = scheduler.timezone_name if scheduler else "Europe/Moscow"
        next_run = scheduler.get_next_run_time() if scheduler else "08:50"

        text = (
            f"📊 *Статус узлов системы (2026):*\n\n"
            f"1. 💻 *Ноутбук (Сервер 24/7):* 🟢 В сети\n"
            f"   - Планировщик: `08:50 ({tz_name})`\n"
            f"   - Следующая сводка: `{next_run}`\n"
            f"   - База дедупликации SQLite: OK\n\n"
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
            await safe_edit_text(event, text, get_main_menu_keyboard(storage.is_chat_mode_active()))
        else:
            await event.answer(clean_telegram_markdown(text), parse_mode="Markdown", reply_markup=get_main_menu_keyboard(storage.is_chat_mode_active()))

    @r.message(Command("sources"))
    @r.callback_query(F.data == "btn_sources")
    async def cmd_sources(event: Message | CallbackQuery):
        sources = load_sources().get("sources", {})
        tg = sources.get("telegram_channels", [])
        rss = sources.get("rss_feeds", [])

        tg_list = "\n".join([f"• @{c.get('username')} _({c.get('category')})_" for c in tg]) or "Нет"
        rss_list = "\n".join([f"• {r.get('name', r.get('url'))} _({r.get('category')})_" for r in rss]) or "Нет"

        text = (
            f"📡 *Подключенные источники для дайджеста:*\n\n"
            f"📢 *Telegram-каналы:*\n{tg_list}\n\n"
            f"📰 *RSS-ленты:*\n{rss_list}\n\n"
            f"_Чтобы добавить канал, просто впишите его в config/sources.yaml на ноутбуке._"
        )
        if isinstance(event, CallbackQuery):
            await event.answer()
            await safe_edit_text(event, text, get_main_menu_keyboard(storage.is_chat_mode_active()))
        else:
            await event.answer(clean_telegram_markdown(text), parse_mode="Markdown", reply_markup=get_main_menu_keyboard(storage.is_chat_mode_active()))

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
                "• `/setmodel gemini gemini-1.5-flash`\n"
                "• `/setmodel groq llama-3.3-70b-versatile`\n"
                "• `/setmodel local qwen2.5-7b-instruct`"
            )
            await msg.answer(clean_telegram_markdown(text), parse_mode="Markdown")
            return

        provider = parts[1].lower()
        model_name = parts[2]

        if provider not in ["local", "groq", "gemini", "openrouter"]:
            await msg.answer("❌ Допустимые провайдеры: `local`, `groq`, `gemini`, `openrouter`", parse_mode="Markdown")
            return

        llm_router.set_model(provider, model_name)
        storage.set_setting(f"model_{provider}", model_name)
        await msg.answer(f"✅ Для провайдера *{provider.upper()}* установлена модель:\n`{model_name}`", parse_mode="Markdown")

    async def send_safe_reply(msg: Message, text: str, reply_markup=None):
        cleaned = clean_telegram_markdown(text)
        try:
            if len(cleaned) > 4000:
                for x in range(0, len(cleaned), 4000):
                    await msg.answer(cleaned[x:x+4000], parse_mode="Markdown")
                if reply_markup:
                    await msg.answer("💬 *Управление диалогом:*", parse_mode="Markdown", reply_markup=reply_markup)
            else:
                await msg.answer(cleaned, parse_mode="Markdown", reply_markup=reply_markup)
        except TelegramBadRequest as e:
            logger.warning(f"Telegram parse error ({e}), retrying as plain text...")
            if len(text) > 4000:
                for x in range(0, len(text), 4000):
                    await msg.answer(text[x:x+4000])
                if reply_markup:
                    await msg.answer("💬 Управление диалогом:", reply_markup=reply_markup)
            else:
                await msg.answer(text, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            await msg.answer(f"⚠️ Ошибка отправки: {e}")

    # --------------------------------------------------------------------------
    # ПРЯМОЙ ЧАТ С ИИ
    # --------------------------------------------------------------------------
    @r.message(F.text & ~F.text.startswith("/"))
    async def on_user_chat_message(msg: Message):
        if not is_admin(msg.from_user.id):
            return

        # Если режим чата выключен — вежливо подсказываем, как его включить
        if not storage.is_chat_mode_active():
            text = (
                f"💡 *Режим диалога сейчас выключен.*\n\n"
                f"Чтобы задать вопрос ИИ или обсудить свежие новости, нажмите кнопку ниже или введите /chat."
            )
            await msg.answer(clean_telegram_markdown(text), parse_mode="Markdown", reply_markup=get_main_menu_keyboard(chat_mode=False))
            return

        await msg.bot.send_chat_action(msg.chat.id, "typing")
        user_query = msg.text

        # Извлекаем последние реплики и свежий дайджест
        history = storage.get_chat_history(limit=6)
        latest_digest = storage.get_latest_digest()

        # Формируем контекст последних новостей из базы
        recent_items = storage.get_news_feed(limit=10)
        recent_news_str = ""
        if recent_items:
            news_parts = []
            for i, it in enumerate(recent_items[:8], 1):
                news_parts.append(f"[{i}] {it.get('title')}\n{it.get('content')[:350]}\nСсылка: {it.get('url')}")
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
