import logging
import time
from datetime import datetime
from typing import Optional, Dict, Any, Tuple, List
import httpx
from openai import AsyncOpenAI
from src.config import LLMConfig, load_prompts
from src.llm.prompts import clean_telegram_markdown

logger = logging.getLogger(__name__)

# Список надежных бесплатных моделей OpenRouter на замену в случае 404
OPENROUTER_FREE_FALLBACKS = [
    "google/gemini-2.0-flash-exp:free",
    "google/gemini-2.0-flash-thinking-exp:free",
    "qwen/qwen-2.5-72b-instruct:free",
    "deepseek/deepseek-chat:free",
    "deepseek/deepseek-r1:free",
    "mistralai/mistral-nemo:free",
    "meta-llama/llama-3.1-8b-instruct:free",
    "meta-llama/llama-3.2-3b-instruct:free"
]

POPULAR_MODELS = {
    "openrouter": [
        ("Gemini 2.0 Flash (Free)", "google/gemini-2.0-flash-exp:free"),
        ("Qwen 2.5 72B (Free)", "qwen/qwen-2.5-72b-instruct:free"),
        ("DeepSeek Chat (Free)", "deepseek/deepseek-chat:free"),
        ("DeepSeek R1 Reasoning (Free)", "deepseek/deepseek-r1:free"),
        ("Gemini 2.0 Thinking (Free)", "google/gemini-2.0-flash-thinking-exp:free"),
        ("Mistral Nemo 12B (Free)", "mistralai/mistral-nemo:free"),
        ("Llama 3.1 8B (Free)", "meta-llama/llama-3.1-8b-instruct:free")
    ],
    "groq": [
        ("Llama 3.3 70B Versatile", "llama-3.3-70b-versatile"),
        ("Llama 3.1 8B Instant", "llama-3.1-8b-instant"),
        ("Gemma 2 9B IT", "gemma2-9b-it")
    ],
    "gemini": [
        ("Gemini 1.5 Flash", "gemini-1.5-flash"),
        ("Gemini 2.0 Flash", "gemini-2.0-flash"),
        ("Gemini 1.5 Pro", "gemini-1.5-pro")
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
            "gemini": config.gemini.model or "gemini-1.5-flash",
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
        """Подбор промпта из config/prompts.yaml под мощность и архитектуру модели"""
        prompts = load_prompts().get("system_prompts", {})

        if task == "chat":
            return prompts.get("direct_chat", "Ты полезный и умный ИИ-ассистент.")

        m_lower = model.lower()
        if provider == "local":
            return prompts.get("local_qwen") or prompts.get("lightweight_models")

        if provider == "gemini" or "gemini" in m_lower:
            return prompts.get("massive_context") or prompts.get("heavy_models")

        if "8b" in m_lower or "nemo" in m_lower or "mini" in m_lower or "3b" in m_lower:
            return prompts.get("lightweight_models") or prompts.get("local_qwen")

        return prompts.get("heavy_models")

    async def check_local_health(self) -> bool:
        """Проверка доступности локального ПК с LM Studio"""
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
        latest_digest: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Универсальная генерация ответа с поддержкой:
        - failover переключения между провайдерами
        - автоматического перебора запасных бесплатных моделей в OpenRouter при 404/429
        - инъекции текущей даты и времени
        - контекста последних новостей
        - очистки от Markdown-заголовков (###)
        """
        start_time = time.time()
        providers_to_try = []

        if self.active_provider == "auto":
            is_local_online = await self.check_local_health()
            if is_local_online:
                providers_to_try.append("local")
            if self.config.groq.enabled and self.config.groq.api_key:
                providers_to_try.append("groq")
            if self.config.gemini.enabled and self.config.gemini.api_key:
                providers_to_try.append("gemini")
            if self.config.openrouter.enabled and self.config.openrouter.api_key:
                providers_to_try.append("openrouter")
        else:
            providers_to_try.append(self.active_provider)

        last_error = None
        for prov in providers_to_try:
            client, initial_model = self._get_client_and_model(prov)
            if not client or not initial_model:
                continue

            # Если выбран OpenRouter — формируем пул бесплатных моделей на случай,
            # если одна из моделей (как llama-3.3-70b-instruct:free) стала платной или временно недоступна
            if prov == "openrouter":
                models_to_test = [initial_model]
                for fb in OPENROUTER_FREE_FALLBACKS:
                    if fb not in models_to_test:
                        models_to_test.append(fb)
            else:
                models_to_test = [initial_model]

            for model in models_to_test:
                base_sys_prompt = override_system_prompt or self.get_prompt_for_model(prov, model, task=task)

                # Если задача — диалог (chat), добавляем динамический контекст реального времени и новостей
                if task == "chat":
                    now = datetime.now()
                    days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
                    date_context = (
                        f"ТЕКУЩАЯ ДАТА И ВРЕМЯ НА СЕРВЕРЕ: {days[now.weekday()]}, {now.strftime('%d.%m.%Y, %H:%M')}.\n"
                        f"Ты абсолютно точно знаешь текущую дату, год ({now.year}) и время.\n"
                        f"Помни: в Telegram не работают решетки ###. Выделяй жирным шрифтом *текст*."
                    )
                    system_prompt = f"{base_sys_prompt}\n\n{date_context}"

                    if latest_digest:
                        system_prompt += (
                            f"\n\nСВЕДЕНИЯ ИЗ ПОСЛЕДНЕГО ВЫПУСКА НОВОСТЕЙ СЕРВЕРА (ты в курсе этих событий и можешь отвечать по ним):\n"
                            f"{latest_digest[:4000]}"
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
                    resp = await client.chat.completions.create(
                        model=model,
                        messages=messages,
                        temperature=0.3 if task == "digest" else 0.7
                    )
                    raw_content = resp.choices[0].message.content
                    content = clean_telegram_markdown(raw_content)

                    # Если сработала запасная модель в OpenRouter — запоминаем её как активную
                    if prov == "openrouter" and model != initial_model:
                        logger.info(f"Модель {initial_model} была недоступна. Автоматически переключено на рабочую {model}.")
                        self.set_model("openrouter", model)

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
                    # Если это 404 (модель больше не бесплатна) или 429 (лимит), продолжаем цикл к следующей модели
                    if "404" in err_msg or "unavailable for free" in err_msg or "429" in err_msg:
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
