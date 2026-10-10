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
    - Нормализует пробелы.
    """
    if not text:
        return ""
    # Удаляем китайские иероглифы
    cleaned = re.sub(r'[\u4e00-\u9fff\u3400-\u4dbf]+', '', text)
    # Удаляем арабскую вязь
    cleaned = re.sub(r'[\u0600-\u06ff\u0750-\u077f\ufb50-\ufdff\ufe70-\ufeff]+', '', cleaned)
    # Нормализуем пробелы
    cleaned = re.sub(r'[ \t]{2,}', ' ', cleaned)
    return cleaned.strip()

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
        recent_news: Optional[str] = None
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

                if task == "chat":
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
                    system_prompt = f"{base_sys_prompt}\n\n{date_context}{language_lock}"

                    # Инъекция контекста новостей (дайджест + свежие статьи из базы)
                    news_block = ""
                    if latest_digest:
                        news_block += f"\n\nСВЕДЕНИЯ ИЗ ПОСЛЕДНЕГО АНАЛИТИЧЕСКОГО ДАЙДЖЕСТА:\n{latest_digest[:4000]}"
                    if recent_news:
                        news_block += (
                            f"\n\nСВЕЖИЕ ПУБЛИКАЦИИ ИЗ ПОДКЛЮЧЕННЫХ КАНАЛОВ (РЕАЛЬНЫЕ ФАКТЫ И СОБЫТИЯ):\n"
                            f"{recent_news[:7000]}\n\n"
                            f"ИНСТРУКЦИЯ ПО ИСПОЛЬЗОВАНИЮ ЭТИХ ДАННЫХ:\n"
                            f"1. Ты обладаешь этой актуальной информацией. Когда пользователь спрашивает о последних событиях, новостях, конкретике по Украине, фронту, заявлениям политиков (Трамп, Путин и др.) или технологиям — ТЫ ОБЯЗАН СТРОГО ИСПОЛЬЗОВАТЬ ЭТИ ПЕРЕДАННЫЕ ФАКТЫ.\n"
                            f"2. Приводи конкретные факты, названия каналов (@Dyadyabatya0, @ross_name, @rybar, РИА, ТАСС, Хакер) и цитируй детали из текста.\n"
                            f"3. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО говорить 'новости не указаны в запросе' или 'у меня нет данных' — все ключевые данные переданы тебе выше!"
                        )

                    if news_block:
                        system_prompt += news_block

                    if prov == "local":
                        system_prompt = (
                            "ВНИМАНИЕ (ДЛЯ ЛОКАЛЬНОЙ МОДЕЛИ QWEN 2.5):\n"
                            "ТЫ — АНАЛИТИК СЕРВЕРА С ДОСТУПОМ К ПУБЛИКАЦИЯМ КАНАЛОВ. "
                            "ОТВЕЧАЙ СТРОГО ПО ФАКТАМ ИЗ ПЕРЕДАННЫХ СТАТЕЙ, ЦИТИРУЙ КАНАЛЫ (@Dyadyabatya0, @ross_name, @rybar, РИА, ТАСС, Хакер). "
                            "ЗАПРЕЩЕНО писать шаблоны вида 'ситуация остается стабильной, реформы для улучшения бизнес-климата'! Пиши живые факты из статей.\n\n"
                            + system_prompt
                        )
                else:
                    system_prompt = base_sys_prompt

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
