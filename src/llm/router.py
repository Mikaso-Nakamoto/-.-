import logging
import time
from typing import Optional, Dict, Any, Tuple
import httpx
from openai import AsyncOpenAI
from src.config import LLMConfig

logger = logging.getLogger(__name__)

class LLMRouter:
    def __init__(self, config: LLMConfig):
        self.config = config
        self.active_provider = config.default_provider

    def set_provider(self, provider: str):
        if provider in ["auto", "local", "groq", "gemini", "openrouter"]:
            self.active_provider = provider
            logger.info(f"Активный провайдер изменен на: {provider}")
            return True
        return False

    async def check_local_health(self) -> bool:
        """Проверка доступности локального ПК с Qwen 2.5"""
        if not self.config.local.enabled:
            return False
        try:
            # Преобразуем URL /v1 в корневой или models для healthcheck
            base = self.config.local.base_url.rstrip("/")
            if base.endswith("/v1"):
                check_url = f"{base}/models"
            else:
                check_url = f"{base}/api/tags" if "11434" in base else f"{base}/v1/models"

            async with httpx.AsyncClient(timeout=2.5) as client:
                resp = await client.get(check_url)
                return resp.status_code == 200
        except Exception as e:
            logger.debug(f"Локальный ПК с Qwen 2.5 недоступен: {e}")
            return False

    def _get_client_and_model(self, provider: str) -> Tuple[Optional[AsyncOpenAI], Optional[str]]:
        timeout = float(self.config.timeout_seconds)

        if provider == "local":
            client = AsyncOpenAI(
                base_url=self.config.local.base_url,
                api_key=self.config.local.api_key or "ollama",
                timeout=timeout
            )
            return client, self.config.local.model

        elif provider == "groq":
            if not self.config.groq.api_key:
                return None, None
            client = AsyncOpenAI(
                base_url="https://api.groq.com/openai/v1",
                api_key=self.config.groq.api_key,
                timeout=timeout
            )
            return client, self.config.groq.model or "llama-3.3-70b-versatile"

        elif provider == "gemini":
            if not self.config.gemini.api_key:
                return None, None
            client = AsyncOpenAI(
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                api_key=self.config.gemini.api_key,
                timeout=timeout
            )
            return client, self.config.gemini.model or "gemini-1.5-flash"

        elif provider == "openrouter":
            if not self.config.openrouter.api_key:
                return None, None
            client = AsyncOpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=self.config.openrouter.api_key,
                timeout=timeout,
                default_headers={"HTTP-Referer": "https://github.com/Mikaso-Nakamoto/-.-"}
            )
            return client, self.config.openrouter.model or "meta-llama/llama-3.3-70b-instruct:free"

        return None, None

    async def generate_response(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        """Генерация ответа с поддержкой авто-переключения (fallback)"""
        start_time = time.time()
        providers_to_try = []

        if self.active_provider == "auto":
            # Проверяем локальный узел первым
            is_local_online = await self.check_local_health()
            if is_local_online:
                providers_to_try.append("local")
            # Облачный пул в качестве резерва или основы
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
            client, model = self._get_client_and_model(prov)
            if not client or not model:
                continue

            try:
                logger.info(f"Запрос к LLM через [{prov}], модель: {model}...")
                resp = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=0.4
                )
                content = resp.choices[0].message.content
                elapsed = round(time.time() - start_time, 2)
                return {
                    "success": True,
                    "content": content,
                    "provider": prov,
                    "model": model,
                    "latency": elapsed,
                    "fallback_occurred": prov != providers_to_try[0]
                }
            except Exception as e:
                logger.warning(f"Ошибка вызова LLM [{prov}]: {e}")
                last_error = str(e)

        return {
            "success": False,
            "content": f"Не удалось получить ответ ни от одного LLM-провайдера. Ошибка: {last_error}",
            "provider": None,
            "model": None,
            "latency": round(time.time() - start_time, 2),
            "fallback_occurred": False
        }
