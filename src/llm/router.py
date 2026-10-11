import os
import re
import time
import logging
from datetime import datetime
from typing import Optional, Dict, Any, Tuple, List
import httpx
from openai import AsyncOpenAI
from src.config import LLMConfig, load_prompts
from src.llm.prompts import clean_telegram_markdown

logger = logging.getLogger(__name__)

def sanitize_language_output(text: str) -> str:
    """
    Очищает ответ модели от случайных глифов и иероглифов, возникающих
    при сбое квантования/внимания мультиязычных локальных моделей (например Qwen 2.5 7B):
    - Удаляет случайные китайские иероглифы ([\u4e00-\u9fff\u3400-\u4dbf]+).
    - Удаляет случайную арабскую вязь ([\u0600-\u06ff\u0750-\u077f\ufb50-\ufdff\ufe70-\ufeff]+).
    - Удаляет случайные английские фразы-паразиты мультиязычных 7B моделей.
    - Нормализует пробелы.
    """
    if not text:
        return ""
    # Удаляем китайские иероглифы
    cleaned = re.sub(r'[\u4e00-\u9fff\u3400-\u4dbf]+', '', text)
    # Удаляем арабскую вязь
    cleaned = re.sub(r'[\u0600-\u06ff\u0750-\u077f\ufb50-\ufdff\ufe70-\ufeff]+', '', cleaned)
    # Удаляем случайные англоязычные фразы-паразиты
    cleaned = re.sub(r'(?i)\b(?:there is no information about|on the current moment|specific attack on|in the provided news)\b', '', cleaned)
    # Нормализуем пробелы
    cleaned = re.sub(r'[ \t]{2,}', ' ', cleaned)
    return cleaned.strip()

def sanitize_placeholders(text: str) -> str:
    """
    Очищает ответ модели от шаблонных галлюцинаций и заглушек (@канал, [@канал](ссылка)).
    """
    if not text:
        return ""
    # Удаляем дублирующие блоки цитат с заглушкой @канал
    if "@канал" in text:
        text = re.sub(r'<blockquote[^>]*>.*?</blockquote>\s*—?\s*@?[а-яa-z0-9_-]*', '', text, flags=re.DOTALL | re.IGNORECASE)
    # Удаляем шаблонные строки источников с заглушками
    text = re.sub(r'—?\s*Источники:\s*\[?@?канал\d*\]?\(?[^\)\n]*\)?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[@?канал\d*\]\((?:ссылка|url\d*)\)', '', text, flags=re.IGNORECASE)
    text = re.sub(r'—?\s*@канал\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[ссылка\]\(ссылка\)', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

# Кэш актуальных бесплатных моделей OpenRouter
_cached_openrouter_free_models: List[str] = []
_last_openrouter_fetch_time: float = 0.0

async def get_live_openrouter_free_models(api_key: str) -> List[str]:
    """Динамический запрос списка реально работающих бесплатных моделей из OpenRouter API"""
    global _cached_openrouter_free_models, _last_openrouter_fetch_time
    now = time.time()
    if _cached_openrouter_free_models and (now - _last_openrouter_fetch_time) < 1800:
        return _cached_openrouter_free_models

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get("https://openrouter.ai/api/v1/models", headers=headers)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                free_slugs = []
                for m in data:
                    mid = m.get("id", "")
                    pricing = m.get("pricing", {})
                    if mid.endswith(":free") or (pricing.get("prompt") == "0" and pricing.get("completion") == "0"):
                        if "llama-3.3-70b" not in mid and "llama-3.1-8b" not in mid:
                            free_slugs.append(mid)
                if free_slugs:
                    _cached_openrouter_free_models = free_slugs
                    _last_openrouter_fetch_time = now
                    logger.info(f"OpenRouter: обнаружено {len(free_slugs)} доступных бесплатных моделей: {free_slugs[:5]}")
                    return free_slugs
    except Exception as e:
        logger.warning(f"Не удалось получить живой список моделей OpenRouter: {e}")

    return [
        "google/gemini-2.0-flash-exp:free",
        "google/gemini-2.0-flash-thinking-exp:free",
        "qwen/qwen-2.5-72b-instruct:free",
        "deepseek/deepseek-chat:free",
        "deepseek/deepseek-r1:free",
        "mistralai/mistral-nemo:free"
    ]

POPULAR_MODELS = {
    "openrouter": [
        ("Gemini 2.0 Flash (Free)", "google/gemini-2.0-flash-exp:free"),
        ("Qwen 2.5 72B (Free)", "qwen/qwen-2.5-72b-instruct:free"),
        ("DeepSeek Chat (Free)", "deepseek/deepseek-chat:free"),
        ("DeepSeek R1 Reasoning (Free)", "deepseek/deepseek-r1:free"),
        ("Mistral Nemo 12B (Free)", "mistralai/mistral-nemo:free")
    ],
    "groq": [
        ("Llama 3.3 70B Versatile", "llama-3.3-70b-versatile"),
        ("Llama 3.1 8B Instant", "llama-3.1-8b-instant"),
        ("Gemma 2 9B IT", "gemma2-9b-it")
    ],
    "gemini": [
        ("Gemini 3.5 Flash (Рекомендуемая 2026)", "gemini-3.5-flash"),
        ("Gemini 3.5 Flash-Lite", "gemini-3.5-flash-lite"),
        ("Gemini 2.5 Flash", "gemini-2.5-flash"),
        ("Gemini 2.0 Flash", "gemini-2.0-flash")
    ],
    "local": [
        ("Qwen 2.5 7B (LM Studio)", "qwen2.5-7b-instruct")
    ]
}

class LLMRouter:
    def __init__(self, config: LLMConfig):
        self.config = config
        self.active_provider = config.default_provider
        self.active_models = {
            "local": config.local.model or "qwen2.5-7b-instruct",
            "groq": config.groq.model or "llama-3.3-70b-versatile",
            "gemini": config.gemini.model or "gemini-3.5-flash",
            "openrouter": config.openrouter.model or "google/gemini-2.0-flash-exp:free"
        }

    def set_provider(self, provider: str) -> bool:
        if provider in ["auto", "local", "groq", "gemini", "openrouter"]:
            self.active_provider = provider
            logger.info(f"Активный провайдер изменен на: {provider}")
            return True
        return False

    def set_model(self, provider: str, model_name: str):
        if provider in self.active_models:
            self.active_models[provider] = model_name
            if provider == "groq":
                self.config.groq.model = model_name
            elif provider == "gemini":
                self.config.gemini.model = model_name
            elif provider == "openrouter":
                self.config.openrouter.model = model_name
            elif provider == "local":
                self.config.local.model = model_name
            logger.info(f"Для провайдера {provider} установлена модель: {model_name}")

    def get_current_model_for_provider(self, provider: str) -> str:
        return self.active_models.get(provider, "default")

    def get_prompt_for_model(self, provider: str, model: str, task: str = "digest") -> str:
        prompts = load_prompts().get("system_prompts", {})

        if task == "chat":
            return prompts.get("direct_chat", "Ты полезный и умный ИИ-ассистент.")

        if task == "curator":
            from src.llm.prompts import AI_CURATOR_SYSTEM_PROMPT
            return prompts.get("ai_curator", AI_CURATOR_SYSTEM_PROMPT)

        m_lower = model.lower()
        if provider == "local":
            return prompts.get("local_qwen") or prompts.get("lightweight_models")

        if provider == "gemini" or "gemini" in m_lower:
            return prompts.get("massive_context") or prompts.get("heavy_models")

        if "8b" in m_lower or "nemo" in m_lower or "mini" in m_lower or "lite" in m_lower:
            return prompts.get("lightweight_models") or prompts.get("local_qwen")

        return prompts.get("heavy_models")

    async def check_local_health(self) -> bool:
        if not self.config.local.enabled:
            return False
        try:
            base = self.config.local.base_url.rstrip("/")
            check_url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(check_url)
                return resp.status_code == 200
        except Exception as e:
            logger.debug(f"Локальный ПК с LM Studio недоступен: {e}")
            return False

    def _get_client_and_model(self, provider: str) -> Tuple[Optional[AsyncOpenAI], Optional[str]]:
        timeout = float(self.config.timeout_seconds)

        if provider == "local":
            client = AsyncOpenAI(
                base_url=self.config.local.base_url,
                api_key=self.config.local.api_key or "lm-studio",
                timeout=timeout
            )
            return client, self.active_models["local"]

        elif provider == "groq":
            if not self.config.groq.api_key:
                return None, None
            client = AsyncOpenAI(
                base_url="https://api.groq.com/openai/v1",
                api_key=self.config.groq.api_key,
                timeout=timeout
            )
            return client, self.active_models["groq"]

        elif provider == "gemini":
            if not self.config.gemini.api_key:
                return None, None
            client = AsyncOpenAI(
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                api_key=self.config.gemini.api_key,
                timeout=timeout
            )
            return client, self.active_models["gemini"]

        elif provider == "openrouter":
            if not self.config.openrouter.api_key:
                return None, None
            client = AsyncOpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=self.config.openrouter.api_key,
                timeout=timeout,
                default_headers={"HTTP-Referer": "https://github.com/Mikaso-Nakamoto/-.-"}
            )
            return client, self.active_models["openrouter"]

        return None, None

    async def generate_response(
        self,
        task: str,
        user_prompt: str,
        override_system_prompt: Optional[str] = None,
        chat_history: Optional[List[Dict[str, str]]] = None,
        latest_digest: Optional[str] = None,
        recent_news: Optional[str] = None,
        web_sources: Optional[str] = None
    ) -> Dict[str, Any]:
        start_time = time.time()
        providers_to_try = []

        if self.active_provider == "auto":
            is_local_online = await self.check_local_health()
            if is_local_online:
                providers_to_try.append("local")
            if self.config.gemini.enabled and self.config.gemini.api_key:
                providers_to_try.append("gemini")
            if self.config.groq.enabled and self.config.groq.api_key:
                providers_to_try.append("groq")
            if self.config.openrouter.enabled and self.config.openrouter.api_key:
                providers_to_try.append("openrouter")
        else:
            providers_to_try.append(self.active_provider)
            # Если выбран конкретный облачный провайдер, но у него нет ключа или он упадет,
            # добавляем резервные в конец очереди
            for backup in ["gemini", "groq", "openrouter", "local"]:
                if backup != self.active_provider and backup not in providers_to_try:
                    providers_to_try.append(backup)

        last_error = None
        for prov in providers_to_try:
            client, initial_model = self._get_client_and_model(prov)
            if not client or not initial_model:
                last_error = f"Для провайдера {prov.upper()} отсутствует API-ключ в .env"
                continue

            # Каскадный пул моделей для каждого провайдера
            if prov == "openrouter":
                live_free = await get_live_openrouter_free_models(self.config.openrouter.api_key)
                models_to_test = [initial_model]
                for fb in live_free:
                    if fb not in models_to_test:
                        models_to_test.append(fb)
            elif prov == "gemini":
                # Google AI Studio официальные слаги (стандарт 2026 года и резерв)
                gemini_pool = [
                    initial_model,
                    "gemini-3.5-flash",
                    "gemini-3.5-flash-lite",
                    "gemini-2.5-flash",
                    "gemini-2.0-flash",
                    "gemini-2.0-flash-lite"
                ]
                models_to_test = []
                for m in gemini_pool:
                    if m not in models_to_test:
                        models_to_test.append(m)
            elif prov == "groq":
                groq_pool = [initial_model, "llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
                models_to_test = [m for m in groq_pool if m]
            else:
                models_to_test = [initial_model]

            for model in models_to_test:
                base_sys_prompt = override_system_prompt or self.get_prompt_for_model(prov, model, task=task)

                now = datetime.now()
                days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
                date_context = (
                    f"ТЕКУЩАЯ ДАТА И ВРЕМЯ НА СЕРВЕРЕ: {days[now.weekday()]}, {now.strftime('%d.%m.%Y, %H:%M')}.\n"
                    f"Ты абсолютно точно знаешь текущую дату, год ({now.year}) и время."
                )
                language_lock = (
                    "\n\nСТРОГОЕ ЯЗЫКОВОЕ ПРАВИЛО (СТРОЖАЙШИЙ ПРИОРИТЕТ):\n"
                    "1. Отвечай ИСКЛЮЧИТЕЛЬНО на грамотном, естественном и связном русском языке.\n"
                    "2. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать китайские иероглифы (中文), "
                    "арабскую вязь (العربية) или смешивать иностранные слова посреди русских предложений.\n"
                    "3. Все имена, географические названия и страны пиши по-русски (Вашингтон, Киев, Пекин, Москва, Харьков, Трамп и т.д.). "
                    "Общепринятые IT-термины (Docker, GPU, LLM) допустимо оставлять на английском, но грамматика всего предложения должна быть СТРОГО русской!\n"
                    "4. При вопросах о мировой геополитике (включая конфликт вокруг Украины, отношения РФ, США, НАТО, ЕС, Китая) "
                    "давай объективный, фактологический и взвешенный анализ на базе свежих новостей и позиций сторон, спокойно и без эмоциональных перекосов."
                )

                if override_system_prompt:
                    # Модуль задал специальный системный промпт — не загрязняем его общими шаблонами
                    system_prompt = f"{override_system_prompt}\n\n{date_context}{language_lock}"
                elif task == "chat":
                    base_sys_prompt = self.get_prompt_for_model(prov, model, task=task)
                    system_prompt = f"{base_sys_prompt}\n\n{date_context}{language_lock}"

                    # Инъекция контекста новостей (дайджест + свежие статьи из базы)
                    news_block = ""
                    max_digest_chars = 2500 if prov == "local" else 6000
                    max_news_chars = 14000 if prov == "local" else 32000

                    if latest_digest:
                        news_block += f"\n\nСВЕДЕНИЯ ИЗ ПОСЛЕДНЕГО АНАЛИТИЧЕСКОГО ДАЙДЖЕСТА:\n{latest_digest[:max_digest_chars]}"
                    if recent_news:
                        news_block += (
                            f"\n\nСВЕЖИЕ ПУБЛИКАЦИИ ИЗ ПОДКЛЮЧЕННЫХ КАНАЛОВ (РЕАЛЬНЫЕ ФАКТЫ, СВО, СОБЫТИЯ, СОФТ):\n"
                            f"{recent_news[:max_news_chars]}\n\n"
                            f"ИНСТРУКЦИЯ ПО ИСПОЛЬЗОВАНИЮ ЭТИХ ДАННЫХ:\n"
                            f"1. СТРОГАЯ ФАКТОЛОГИЯ И ЗАПРЕТ ГАЛЛЮЦИНАЦИЙ: Опирайся ИСКЛЮЧИТЕЛЬНО на реальные факты из статей. "
                            f"Категорически запрещено путать стороны конфликта или додумывать нелепые взаимосвязи (например: Россия НЕ поставляет дизель Украине; Трамп критиковал удары по российским НПЗ). "
                            f"Если факт прямо не следует из текста, не придумывай фантастических объяснений — излагай только то, что конкретно сообщили каналы.\n"
                            f"2. НИКАКИХ ДУБЛИКАТОВ В ОТВЕТЕ: Каждая новость или событие должны упоминаться РОВНО ОДИН РАЗ! "
                            f"Запрещено повторять одну и ту же новость в разных пунктах под видом 'дайджеста' или 'сводки'. "
                            f"Цитату спикера или очевидца вставляй СТРОГО ВНУТРЬ того пункта, к которому она относится, а не отдельным дублирующим блоком в конце.\n"
                            f"3. РЕАЛЬНЫЕ ИСТОЧНИКИ: Указывай конкретное название канала из предоставленных статей (@ross_name, @infomil_live, @rian_ru). "
                            f"КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО писать шаблоны-заглушки вида '— @канал', '[@канал](ссылка)' или '[ссылка]'.\n"
                            f"4. МНОГОИСТОЧНИКОВЫЙ СИНТЕЗ: Если несколько каналов (@Voennaja_svodka, @infomil_live, @ross_name, @Dyadyabatya0, @rybar, РИА, ТАСС) пишут об одном инциденте — "
                            f"ОБЪЕДИНИ все факты в один плотный связный абзац по теме, не повторяя одинаковые вещи, но дополняя уникальные детали из каждого источника (направления, объекты, мосты, склады, повреждения, работа ПВО).\n"
                            f"5. ЦИТИРОВАНИЕ: Выделяй важные фразы авторов через <blockquote>«текст цитаты» — @название_канала</blockquote>.\n"
                            f"6. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО говорить 'новости не указаны в запросе', 'нет информации' или сочинять выдуманные общие фразы! Отвечай строго по реальной фактуре из контекста."
                        )
                    if web_sources:
                        news_block += (
                            f"\n\nСВЕЖИЕ МАТЕРИАЛЫ ИЗ ВНЕШНЕГО ВЕБ-ПОИСКА (АВТОРИТЕТНЫЕ МИРОВЫЕ И ПРОФИЛЬНЫЕ РЕСУРСЫ):\n"
                            f"{web_sources[:max_news_chars]}\n\n"
                            f"ИНСТРУКЦИЯ ПО ОФОРМЛЕНИЮ ОТВЕТА (СТИЛЬ PERPLEXITY / OSINT РАЗВЕДКА):\n"
                            f"1. Ответь максимально полно, развернуто и фактологично, опираясь на найденные веб-источники.\n"
                            f"2. Обязательно структурируй ответ на понятные блоки с жирными подзаголовками:\n"
                            f"   - Краткий итог / главное событие (в 1-2 предложениях).\n"
                            f"   - Предыстория и суть обвинений / процесса / проблемы (списком с жирными маркерами: • <b>Пункт</b>: описание).\n"
                            f"   - Финал, суммы, последствия, комментарии или текущий статус.\n"
                            f"3. Обязательно подтверждай утверждения кликабельными ссылками на первоисточники в формате: <a href=\"URL\">[Название СМИ]</a> (например, <a href=\"...\">[BBC]</a>, <a href=\"...\">[Коммерсантъ]</a>).\n"
                            f"4. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО писать отписки вроде 'информация не найдена' или 'в новостях не указано'! Вся фактура уже найдена в блоке веб-поиска выше — изложи её качественно и профессионально."
                        )

                    if news_block:
                        system_prompt += news_block

                    if prov == "local":
                        system_prompt = (
                            "ВНИМАНИЕ (ДЛЯ ЛОКАЛЬНОЙ МОДЕЛИ QWEN 2.5 НА RTX / LM STUDIO):\n"
                            "ТЫ — АВТОНОМНЫЙ АНАЛИТИК СЕРВЕРА С ДОСТУПОМ КО ВСЕМ ПУБЛИКАЦИЯМ КАНАЛОВ.\n"
                            "1. СТРОГАЯ ФАКТОЛОГИЯ БЕЗ ГАЛЛЮЦИНАЦИЙ: Не путай стороны конфликта и не сочиняй взаимоисключающих вещей. "
                            "Опирайся только на конкретный текст переданных статей.\n"
                            "2. ЗАПРЕТ ДУБЛИРОВАНИЯ: Не повторяй одно и то же событие в разных пунктах! Объединяй все факты по теме в один цельный пункт.\n"
                            "3. КАТЕГОРИЧЕСКИЙ ЗАПРЕТ ЗАГЛУШЕК: Никогда не пиши шаблоны-заглушки вида '— @канал', '[@канал](ссылка)'. Называй только реальные каналы из статей.\n"
                            "4. МАКСИМУМ КОНКРЕТИКИ: Называй точные населенные пункты, направления, типы вооружений, объекты и цифры.\n"
                            "5. ЯЗЫК: Строго чистый, грамотный русский язык.\n\n"
                            + system_prompt
                        )
                elif task == "digest":
                    base_sys_prompt = self.get_prompt_for_model(prov, model, task=task)
                    system_prompt = base_sys_prompt
                    if prov == "local":
                        system_prompt = (
                            "ВНИМАНИЕ (ДЛЯ ЛОКАЛЬНОЙ МОДЕЛИ QWEN 2.5):\n"
                            "ТЫ — АНАЛИТИК И ШЕФ-РЕДАКТОР СЕРВЕРА. "
                            "ОТВЕЧАЙ СТРОГО ПО ФАКТАМ ИЗ ПЕРЕДАННЫХ СТАТЕЙ, ОБЪЕДИНЯЙ ДУБЛИРУЮЩИЕСЯ СОБЫТИЯ В ОДИН ПЛОТНЫЙ АБЗАЦ. "
                            "ОХВАТЫВАЙ ВСЕ СФЕРЫ: СВО, РЕГИОНЫ, ГЕОПОЛИТИКУ, ПОЛЕЗНЫЙ СОФТ, КИБЕРБЕЗОПАСНОСТЬ И ТЕХНОЛОГИИ. "
                            "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО ИСПОЛЬЗОВАТЬ СЫРЫЕ ТАБЛИЦЫ, АНГЛИЙСКИЙ ЯЗЫК, ШАБЛОНЫ '@канал' ИЛИ ВЫДУМКИ.\n\n"
                            + system_prompt
                        )
                else:
                    system_prompt = self.get_prompt_for_model(prov, model, task=task)

                messages = [{"role": "system", "content": system_prompt}]
                if chat_history:
                    for h in chat_history:
                        messages.append({"role": h["role"], "content": h["content"]})
                messages.append({"role": "user", "content": user_prompt})

                try:
                    logger.info(f"Запрос к LLM [{prov}], модель: {model}, задача: {task}...")
                    is_local = (prov == "local")
                    # Для локальной Qwen 2.5 7B понижаем температуру и добавляем штрафы за повторы против сбоя языков
                    temperature = 0.3 if task == "digest" else (0.35 if is_local else 0.6)

                    call_kwargs = {
                        "model": model,
                        "messages": messages,
                        "temperature": temperature,
                        "top_p": 0.9
                    }
                    if is_local:
                        call_kwargs["frequency_penalty"] = 0.15
                        call_kwargs["presence_penalty"] = 0.05

                    resp = await client.chat.completions.create(**call_kwargs)
                    raw_content = resp.choices[0].message.content or ""
                    # Защита от мультиязычного сбоя токенов для локальных моделей
                    sanitized_content = sanitize_language_output(raw_content)
                    sanitized_content = sanitize_placeholders(sanitized_content)
                    content = clean_telegram_markdown(sanitized_content)

                    if model != initial_model:
                        logger.info(f"Провайдер {prov}: успешно ответила резервная модель {model}. Запоминаем её.")
                        self.set_model(prov, model)

                    elapsed = round(time.time() - start_time, 2)
                    return {
                        "success": True,
                        "content": content,
                        "provider": prov,
                        "model": model,
                        "latency": elapsed,
                        "fallback_occurred": (prov != providers_to_try[0]) or (model != initial_model)
                    }
                except Exception as e:
                    err_msg = str(e)
                    logger.warning(f"Ошибка вызова LLM [{prov}] на модели [{model}]: {err_msg}")
                    last_error = err_msg
                    # Если ошибка 404 (модель не найдена или не бесплатна) или 429 (лимит) — пробуем следующую
                    if "404" in err_msg or "not found" in err_msg.lower() or "unavailable for free" in err_msg or "429" in err_msg:
                        continue
                    else:
                        break

        return {
            "success": False,
            "content": f"Не удалось получить ответ ни от одного LLM-провайдера. Ошибка: {last_error}",
            "provider": None,
            "model": None,
            "latency": round(time.time() - start_time, 2),
            "fallback_occurred": False
        }
