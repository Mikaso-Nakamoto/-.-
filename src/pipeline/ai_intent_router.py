import json
import logging
import re
from typing import Dict, Any, List, Optional

from src.pipeline.semantic_search import SemanticSearchEngine
from src.llm.router import LLMRouter

logger = logging.getLogger(__name__)

class AIIntentRouter:
    """
    Интеллектуальный маршрутизатор входящих запросов пользователя.
    Определяет намерения (Intent Classification) и автоматически выбирает нужный инструмент:
    - find_post: поиск конкретного поста/видео/ссылки в каналах или архивах Telegram
    - deep_search: глубокое исследование темы, мировой поиск фактов, OSINT, утечки, Darknet/Tor
    - digest: генерация аналитической сводки новостей / дайджеста
    - status: проверка состояния серверов, видеокарты, LM Studio, прокси
    - chat: обычный диалог, рассуждение, помощь в программировании или консультация
    """

    def __init__(self, semantic_engine: Optional[SemanticSearchEngine] = None):
        self.semantic_engine = semantic_engine or SemanticSearchEngine()

    def fast_heuristic_classify(self, text: str) -> Optional[Dict[str, Any]]:
        """
        Мгновенная эвристическая классификация без задержки LLM (0 мс)
        для очевидных паттернов и ключевых триггеров.
        """
        raw = text.strip()
        q_low = raw.lower()

        # 1. Запрос статуса системы
        if re.search(r'(?i)\b(?:как\s+там\s+сервер|статус\s+сервера|состояние\s+сервера|здоровье\s+ноды|статус\s+системы|жив\s+ли\s+бот|видит\s+ли\s+видеокарт|что\s+с\s+lm\s*studio|доступен\s+ли\s+пк|сервер\s+в\s+строю)\b', q_low):
            return {
                "action": "status",
                "clean_query": raw,
                "target_channels": [],
                "include_darknet": False,
                "confidence": 0.98,
                "reason": "Запрос диагностики серверов и узлов"
            }

        # 2. Запрос дайджеста / сводки
        digest_pat = r'(?i)\b(?:сделай|подготовь|дай|собери|пришли|покажи|сформируй)\b.*?\b(?:сводк[а-я]*|дайджест[а-я]*)\b|\b(?:утренняя|вечерняя|ночная|главная)\s+сводк[а-я]*\b|\bкартин[а-я]*\s+дня\b|\bновости\s+за\s+сегодня\b'
        if re.search(digest_pat, q_low):
            clean_focus = re.sub(r'(?i)\b(?:сделай|подготовь|дай|собери|пришли|покажи|сформируй|мне|утреннюю|вечернюю|сводку|сводка|дайджест|новости|за\s+сегодня|картину\s+дня|пожалуйста)\b', '', raw).strip(' .,:;-')
            return {
                "action": "digest",
                "clean_query": clean_focus,
                "target_channels": [],
                "include_darknet": False,
                "confidence": 0.95,
                "reason": "Запрос формирования дайджеста / сводки"
            }

        # 3. Запрос поиска конкретного поста в Telegram-каналах
        find_triggers = [
            r'в\s+каком[- ]то\s+посте',
            r'был\s+пост\s+про',
            r'вроде\s+был\s+пост',
            r'помню\s+пост',
            r'найди\s+(?:мне\s+)?пост',
            r'найди\s+в\s+канале',
            r'скинь\s+ссылку\s+на\s+пост',
            r'дай\s+ссылку\s+на\s+пост',
            r'видел\s+в\s+канале',
            r'пост\s+где\s+было',
            r'найди\s+видео\s+в\s+канале'
        ]
        if any(re.search(pat, q_low) for pat in find_triggers):
            clean_q, targets = self.semantic_engine.extract_search_intent(raw)
            return {
                "action": "find_post",
                "clean_query": clean_q,
                "target_channels": targets,
                "include_darknet": False,
                "confidence": 0.95,
                "reason": "Запрос поиска конкретной публикации в каналах"
            }

        # 4. Явный запрос глубокого поиска / разведки / даркнета
        search_triggers = [
            r'^(?:поиск|поищи|пробей|найди\s+информацию|найди\s+инфу|глубокий\s+поиск|основательный\s+поиск)\b',
            r'\b(?:найди\s+в\s+интернете|поищи\s+в\s+вебе|найди\s+в\s+вебе|внешний\s+поиск|пробей\s+по\s+вебу)\b',
            r'(?:даркнет|onion|\.onion|ahmia|сети\s+tor|теневом\s+сегменте|утечк|слив\s+баз)'
        ]
        is_darknet = bool(re.search(r'(?i)(?:даркнет|onion|\.onion|ahmia|сети\s+tor|теневом\s+сегменте|0-day|эксплойт|слив|дамп|утечк)', q_low))
        if any(re.search(pat, q_low) for pat in search_triggers):
            clean_q = re.sub(r'(?i)^(?:поиск:?|поищи|пробей|найди информацию про|найди инфу про|глубокий поиск:?|найди в интернете|найди в даркнете|пробей по даркнету)\s*', '', raw).strip(' .,:;-')
            clean_q, targets = self.semantic_engine.extract_search_intent(clean_q or raw)
            return {
                "action": "deep_search",
                "clean_query": clean_q or raw,
                "target_channels": targets,
                "include_darknet": is_darknet,
                "confidence": 0.92,
                "reason": "Явный запрос на внешний веб/OSINT поиск"
            }

        return None

    async def classify(self, text: str, llm_router: Optional[LLMRouter] = None) -> Dict[str, Any]:
        """
        Основной метод классификации:
        1. Сначала пробует мгновенную эвристику (0 мс).
        2. Если запрос нетривиальный (например 'иск против цукерберга и Meta 40 штатов') —
           опрашивает ИИ (локальный Qwen 2.5 или облако) для точной семантической маршрутизации.
        3. Если ИИ недоступен — применяет интеллектуальный контентный фоллбэк.
        """
        raw = text.strip()
        fast_res = self.fast_heuristic_classify(raw)
        if fast_res:
            return fast_res

        # 2. Опрос LLM для классификации намерений
        if llm_router:
            try:
                system_prompt = (
                    "ТЫ — АВТОНОМНЫЙ ИНТЕЛЛЕКТУАЛЬНЫЙ МАРШРУТИЗАТОР ЗАПРОСОВ АНАЛИТИЧЕСКОГО АГЕНТА «СНОУДЕН».\n"
                    "Твоя задача — проанализировать запрос пользователя и определить, какую операцию лучше всего выполнить.\n\n"
                    "ДОПУСТИМЫЕ ДЕЙСТВИЯ (action):\n"
                    "1. 'find_post' — пользователь ищет конкретный пост, видео, цитату или ссылку из каналов Telegram.\n"
                    "2. 'deep_search' — запрос фактов о событии, судебном иске, персоне, политике, войне, IT-уязвимости, компании, новостях (стиль Perplexity / OSINT).\n"
                    "3. 'digest' — пользователь просит подготовить структурированную сводку новостей / дайджест за день или по теме.\n"
                    "4. 'status' — пользователь интересуется состоянием бота, серверов, оборудования, LM Studio.\n"
                    "5. 'chat' — приветствие, беседа, программирование/код, математика, совет или общий вопрос без необходимости внешнего поиска.\n\n"
                    "СТРОГИЕ ПРАВИЛА ВЫХОДА:\n"
                    "Отвечай ИСКЛЮЧИТЕЛЬНО валидным JSON-объектом без лишнего текста, без кавычек ```json:\n"
                    "{\n"
                    '  "action": "find_post" | "deep_search" | "digest" | "status" | "chat",\n'
                    '  "clean_query": "краткая фактологическая суть запроса для поисковика",\n'
                    '  "target_channels": ["список упомянутых юзернеймов каналов или []"],\n'
                    '  "include_darknet": true/false (true если запрос касается уязвимостей, 0-day, хакеров, утечек баз данных или сети Tor)\n'
                    "}"
                )

                user_prompt = f"Запрос пользователя:\n«{raw}»"
                res = await llm_router.generate_response(
                    task="classification",
                    user_prompt=user_prompt,
                    override_system_prompt=system_prompt
                )

                if res.get("success") and res.get("content"):
                    content = res["content"].strip()
                    m = re.search(r'\{.*\}', content, re.DOTALL)
                    if m:
                        data = json.loads(m.group(0))
                        action = data.get("action", "chat")
                        if action in ["find_post", "deep_search", "digest", "status", "chat"]:
                            clean_q = data.get("clean_query") or raw
                            channels = data.get("target_channels") or []
                            darknet = bool(data.get("include_darknet", False))
                            logger.info(f"🧠 LLM Intent Classifier: action={action}, query='{clean_q}', darknet={darknet}")
                            return {
                                "action": action,
                                "clean_query": clean_q,
                                "target_channels": channels,
                                "include_darknet": darknet,
                                "confidence": 0.90,
                                "reason": "Классифицировано нейросетью"
                            }
            except Exception as e:
                logger.warning(f"Ошибка LLM-классификатора намерений: {e}")

        # 3. Фоллбэк: анализ семантической формы запроса
        # Если это фактологический вопрос о событии, компании, персоне, иске, войне (например "иск против цукерберга...") -> deep_search
        q_low = raw.lower()
        is_informational_or_factual = any(w in q_low for w in [
            "иск", "суд", "штат", "цукерберг", "meta", "google", "apple", "microsoft",
            "трамп", "путин", "сво", "удар", "дрон", "бпла", "санкци", "нпз", "переговор",
            "уязвимост", "0-day", "взлом", "хакер", "cve", "парол", "утечк", "что с", "что произошло",
            "почему", "сколько", "кто такой", "что такое"
        ]) or (len(raw.split()) >= 3 and not re.search(r'(?i)\b(?:привет|здравствуй|спасибо|напиши код|помоги с|как дела)\b', q_low))

        if is_informational_or_factual:
            clean_q, targets = self.semantic_engine.extract_search_intent(raw)
            is_darknet = bool(re.search(r'(?i)\b(?:даркнет|onion|0-day|эксплойт|слив|утечк|cve)\b', q_low))
            return {
                "action": "deep_search",
                "clean_query": clean_q,
                "target_channels": targets,
                "include_darknet": is_darknet,
                "confidence": 0.80,
                "reason": "Фоллбэк: информационный фактологический запрос"
            }

        return {
            "action": "chat",
            "clean_query": raw,
            "target_channels": [],
            "include_darknet": False,
            "confidence": 0.70,
            "reason": "Диалоговое сообщение"
        }
