import json
import logging
import re
from typing import Dict, Any, List, Optional

from src.llm.router import LLMRouter
from src.config import load_prompts

logger = logging.getLogger(__name__)

class QueryDecomposer:
    """
    Фаза 1: Декомпозиция и семантическое развертывание запроса.
    Превращает абстрактные или широкие запросы (например: «новое оружие России, о беспилотниках»)
    в структурированную матрицу конкретных моделей, названий, систем и целевых запросов.
    """

    @staticmethod
    def _parse_json_result(content: str, default_query: str) -> Dict[str, Any]:
        """Безопасный парсинг JSON из вывода нейросети"""
        try:
            m = re.search(r'\{.*\}', content.strip(), re.DOTALL)
            if m:
                data = json.loads(m.group(0))
                entities = data.get("specific_entities") or []
                queries = data.get("search_queries") or []
                return {
                    "core_topic": data.get("core_topic") or default_query,
                    "specific_entities": entities,
                    "search_queries": queries or [default_query]
                }
        except Exception as e:
            logger.debug(f"JSON parse error in decomposer: {e}")
        return {
            "core_topic": default_query,
            "specific_entities": [],
            "search_queries": [default_query]
        }

    @staticmethod
    def fallback_decompose(query: str) -> Dict[str, Any]:
        """Быстрая эвристическая декомпозиция без ожидания LLM"""
        q_low = query.lower()
        entities = []
        search_queries = [query]

        # 1. Извлечение явных моделей с дефисами (Герань-5, Ланцет-3, Су-57, Т-90М, F-16, и т.д.)
        hyphen_models = re.findall(r'\b[A-Za-zА-Яа-яЁё0-9]+-[A-Za-zА-Яа-яЁё0-9]+\b', query)
        for mod in hyphen_models:
            if not mod.lower().startswith(("кто-", "что-", "как-", "где-", "когда-", "по-")):
                entities.append(mod)

        # 2. Извлечение аппаратных названий и моделей (RTX 5090, RX 8900, Ryzen 9800X3D, Core Ultra, M4 Pro)
        hardware_matches = re.findall(r'\b(?:RTX|GTX|RX|Radeon|GeForce|Ryzen|Core\s*Ultra|M[1-4]\s*(?:Max|Pro)?)\s*[A-Za-z0-9]+(?:\s*[A-Za-z0-9]+)?\b', query, re.IGNORECASE)
        entities.extend(hardware_matches)

        # 3. Извлечение CVE идентификаторов
        cve_matches = re.findall(r'\bCVE-\d{4}-\d+\b', query, re.IGNORECASE)
        entities.extend(cve_matches)

        # 4. Извлечение закавыченных терминов («...» или "...")
        quoted_matches = re.findall(r'["«]([^"»]+)["»]', query)
        entities.extend(quoted_matches)

        # 5. Тематические эвристики предметных областей
        if any(w in q_low for w in ["беспилотник", "дрон", "бпла", "оружи", "герань", "ланцет"]):
            entities.extend([
                "Герань-5", "Герань-3", "Князь Вандал", "Ланцет", "Италмас",
                "Молния", "Охотник С-70", "Куб-БЛА", "дрон на оптоволокне"
            ])
            search_queries.extend([
                "Герань-5 беспилотник характеристики",
                "новые российские дроны СВО",
                "дрон Князь Вандал оптоволокно"
            ])
        elif any(w in q_low for w in ["продвижен", "наступлен", "фронт", "войск"]):
            entities.extend([
                "Покровское направление", "Курахово", "Купянск", "Часов Яр",
                "Угледар", "Торецк", "штурмовые группы ВС РФ"
            ])
            search_queries.extend([
                "продвижение ВС РФ Покровск Курахово",
                "сводка наступления Купянск"
            ])
        elif any(w in q_low for w in ["трамп", "иран", "войн"]):
            entities.extend([
                "Дональд Трамп", "Иран", "ядерная программа", "удары по Ирану",
                "Ближний Восток", "переговоры"
            ])
            search_queries.extend([
                "заявления Трампа война Иран",
                "Trump Iran conflict statement"
            ])
        elif any(w in q_low for w in ["уязвим", "0-day", "эксплойт", "взлом", "хакер"]):
            entities.extend([
                "0-day exploit", "RCE", "CVE", "уязвимость браузера",
                "Linux kernel exploit", "утечка базы данных"
            ])
            search_queries.extend([
                "0-day vulnerability exploit",
                "critical CVE breach"
            ])

        return {
            "core_topic": query.strip(),
            "specific_entities": list(dict.fromkeys(entities)),
            "search_queries": list(dict.fromkeys(search_queries))[:4]
        }

    async def decompose(self, query: str, llm_router: Optional[LLMRouter] = None) -> Dict[str, Any]:
        """
        Декомпозирует запрос с помощью локальной модели Qwen (или активной LLM).
        Если модель недоступна — использует интеллектуальный эвристический справочник.
        """
        raw = query.strip()
        if not llm_router:
            return self.fallback_decompose(raw)

        prompts = load_prompts().get("system_prompts", {})
        system_prompt = prompts.get("query_decomposer") or (
            "ТЫ — АВТОНОМНЫЙ АНАЛИТИК И ПЛАНИРОВЩИК ПОИСКОВЫХ ЗАПРОСОВ («АГЕНТ СНОУДЕН»).\n"
            "Твоя задача — развернуть широкий или общий запрос пользователя в структурированную матрицу поиска:\n"
            "1. Выдели конкретные названия систем, моделей, марки, термины, кодовые имена (например: Герань-5, Ланцет, Князь Вандал, CVE, Покровск).\n"
            "2. Сформируй 2-4 точечных поисковых запроса для каналов и открытого интернета.\n\n"
            "ОТВЕЧАЙ ИСКЛЮЧИТЕЛЬНО ВАЛИДНЫМ JSON БЕЗ ЛИШНЕГО ТЕКСТА:\n"
            "{\n"
            '  "core_topic": "краткая суть темы",\n'
            '  "specific_entities": ["модель 1", "система 2", "название 3"],\n'
            '  "search_queries": ["точечный запрос 1", "точечный запрос 2"]\n'
            "}"
        )

        try:
            res = await llm_router.generate_response(
                task="decomposer",
                user_prompt=f"Запрос пользователя:\n«{raw}»",
                override_system_prompt=system_prompt
            )
            if res.get("success") and res.get("content"):
                parsed = self._parse_json_result(res["content"], raw)
                if parsed.get("specific_entities") or parsed.get("search_queries"):
                    logger.info(f"🧩 Декомпозиция запроса «{raw[:40]}»: найдено {len(parsed['specific_entities'])} конкретных сущностей")
                    return parsed
        except Exception as e:
            logger.debug(f"Ошибка LLM декомпозиции, переход на эвристику: {e}")

        return self.fallback_decompose(raw)
