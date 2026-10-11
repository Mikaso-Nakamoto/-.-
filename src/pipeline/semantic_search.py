import logging
import math
import os
import re
from typing import List, Dict, Any, Optional

try:
    import httpx
except ImportError:
    httpx = None

logger = logging.getLogger(__name__)

class SemanticSearchEngine:
    """
    Семантический поисковый движок по базе постов Telegram-каналов.
    Поддерживает:
    1. Векторные эмбеддинги через локальную модель в LM Studio (text-embedding-nomic-embed-text-v1.5).
    2. Плотный семантический поиск по ключевым концептам и синонимам в SQLite.
    """
    def __init__(self, base_url: Optional[str] = None, model: str = "text-embedding-nomic-embed-text-v1.5"):
        resolved_url = base_url or os.getenv("LOCAL_EMBEDDING_URL") or os.getenv("LOCAL_QWEN_URL") or "http://192.168.0.181:1234/v1"
        self.base_url = resolved_url.rstrip("/")
        self.model = model

    async def get_embedding(self, text: str) -> Optional[List[float]]:
        """Получает 768-мерный вектор эмбеддинга через LM Studio API"""
        if not httpx:
            return None
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                res = await client.post(
                    f"{self.base_url}/embeddings",
                    json={"model": self.model, "input": text[:2000]}
                )
                if res.status_code == 200:
                    data = res.json()
                    return data["data"][0]["embedding"]
        except Exception:
            pass
        return None

    @staticmethod
    def cosine_similarity(v1: List[float], v2: List[float]) -> float:
        dot = sum(a * b for a, b in zip(v1, v2))
        norm1 = math.sqrt(sum(a * a for a in v1))
        norm2 = math.sqrt(sum(b * b for b in v2))
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot / (norm1 * norm2)

    @staticmethod
    def extract_search_intent(query: str) -> tuple[str, List[str]]:
        """
        Очищает запрос от разговорных префиксов и извлекает каналы, если пользователь их упомянул.
        Например: 'в каком-то посте в Военной сводке или в Осведомителе вроде был пост про то, что X. Найди мне этот пост...'
        -> clean_query: 'X'
        -> target_channels: ['voennaja_svodka', 'infomil_live']
        """
        raw = query.strip()
        channel_map = {
            "военн": "voennaja_svodka",
            "сводк": "voennaja_svodka",
            "осведомител": "infomil_live",
            "инфомил": "infomil_live",
            "медведев": "medvedev_telegram",
            "рыбар": "rybar",
            "росс": "ross_name",
            "дядя батя": "dyadyabatya0",
            "хакер": "xakep_ru",
            "хабр": "habr_com",
            "разраб": "proglib",
            "царьград": "rostov_tsargrad",
        }
        targets = []
        q_low = raw.lower()
        for k, v in channel_map.items():
            if k in q_low and v not in targets:
                targets.append(v)

        # Удаление вводных фраз ('в каком-то посте в канале X вроде было про то, что...')
        lead_patterns = [
            r'^(?:в\s+каком[- ]то\s+посте|в\s+посте|помню\s+пост|был\s+пост|видел\s+пост)[^\.\n]*?(?:про\s+то,\s*что|про\s+то\s+что|про\s+|о\s+том,\s*что|о\s+том\s+что|о\s+|где\s+)',
            r'^(?:найди\s+мне\s+сведения\s+о\s+|найди\s+сведения\s+о\s+|найди\s+мне\s+информацию\s+о\s+|найди\s+информацию\s+о\s+|найди\s+мне\s+пост\s+где|найди\s+пост\s+про\s+|найди\s+инфу\s+про\s+|найди\s+мне\s+|найди\s+)',
            r'^(?:расскажи\s+про\s+|что\s+слышно\s+про\s+|что\s+там\s+с\s+)',
        ]
        cleaned = raw
        for p in lead_patterns:
            cleaned = re.sub(p, '', cleaned, flags=re.IGNORECASE).strip()

        # Удаление временных рамок ('за последнюю неделю', 'за последний месяц')
        cleaned = re.sub(r'(?i)\bза\s+последн(?:юю|ий|ие)\s+(?:недел[а-я]*|месяц[а-я]*|дн[а-я]*|сут[а-я]*)\b', '', cleaned).strip()

        # Удаление хвостовых просьб ('найди мне этот пост в канале и дай ссылку')
        cleaned = re.sub(
            r'(?i)\.?[ \t]*(?:найди\s+мне\s+этот\s+пост|найди\s+этот\s+пост|найди\s+пост|скинь\s+ссылку|скинь\s+на\s+него\s+ссылку|дай\s+ссылку|дай\s+его\s+сюда|скинь\s+сюда|дай\s+сюда)[^\.\n]*$',
            '',
            cleaned
        ).strip()
        cleaned = re.sub(r'^(?:что|про|о|об|как)\s+', '', cleaned, flags=re.IGNORECASE).strip(' .,:;-')

        return cleaned or raw, targets

    async def search_posts(self, query: str, candidates: List[Dict[str, Any]], limit: int = 5, target_channels: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Ищет публикации по смысловому описанию.
        Если доступен векторный сервер LM Studio — ранжирует по косинусному сходству.
        В ином случае — использует плотный семантический скоринг.
        """
        if not candidates:
            return []

        norm_targets = [t.lower().lstrip('@') for t in (target_channels or [])]

        query_emb = await self.get_embedding(query)
        if query_emb:
            scored = []
            for it in candidates:
                ch_name = (it.get('channel') or '').lower().lstrip('@')
                is_target_ch = any(t in ch_name for t in norm_targets) if norm_targets else False
                text_sample = f"{it.get('title', '')} {it.get('content', '')[:600]}"
                it_emb = await self.get_embedding(text_sample)
                if it_emb:
                    sim = self.cosine_similarity(query_emb, it_emb)
                    # Базовая релевантность обязана быть не ниже 0.35!
                    if sim >= 0.35:
                        final_sim = sim + (0.15 if is_target_ch else 0.0)
                        scored.append((final_sim, it))
            scored.sort(key=lambda x: x[0], reverse=True)
            return [it for _, it in scored[:limit]]

        # Фоллбэк: семантический поиск по стеммам и ключевым концептам
        words = re.findall(r'[а-яa-z0-9_-]+', query.lower())
        stop_words = {
            "что", "как", "где", "когда", "найди", "пост", "канале", "видео", "вроде", "помню", "знаю", "про", "для", "или", "это",
            "мне", "сведения", "сведений", "информация", "информацию", "данные", "заявление", "заявления",
            "последний", "последнюю", "последние", "неделю", "недели", "недель", "месяц", "месяца", "время", "днях",
            "относительно", "поводу", "расскажи", "покажи", "дай", "были", "было", "был", "есть"
        }
        stems = []
        for w in words:
            if len(w) <= 2 or w in stop_words:
                continue
            stem = re.sub(r'(?:овали|евали|ован|еван|али|яли|или|ила|ала|ели|ать|ять|ить|еть|ского|ских|ские|ской|ская|ское|ск|ого|его|ому|ему|ыми|ими|ами|ями|ых|их|ах|ях|ия|ие|ые|ое|ее|ом|ем|ой|ей|ам|ям|а|я|о|е|у|ю|ы|и)$', '', w)
            stems.append(stem if len(stem) >= 3 else w)
        stems = list(dict.fromkeys(stems))

        scored = []
        for it in candidates:
            ch_name = (it.get('channel') or '').lower().lstrip('@')
            is_target_ch = any(t in ch_name for t in norm_targets) if norm_targets else False
            corpus = f"{it.get('channel', '')} {it.get('title', '')} {it.get('content', '')}".lower()
            
            # Считаем реальные совпадения по стеммам запроса в тексте поста
            matched_stems = 0
            for st in stems:
                if st in corpus:
                    matched_stems += 1

            # КРИТИЧНО: пост ОБЯЗАН иметь совпадение по теме запроса (хотя бы 1 совпадение по ключевому слову)!
            # Принадлежность к каналу даёт бонус, но НЕ даёт права пропускать оффтоп!
            if matched_stems >= 1:
                score = matched_stems * 25
                if is_target_ch:
                    score += 40
                if (matched_stems >= 2) or (is_target_ch and matched_stems >= 1) or (len(stems) <= 2 and matched_stems >= 1):
                    scored.append((score, it))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [it for _, it in scored[:limit]]
