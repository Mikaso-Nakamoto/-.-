import logging
import math
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
    def __init__(self, base_url: str = "http://192.168.1.150:1234/v1", model: str = "text-embedding-nomic-embed-text-v1.5"):
        self.base_url = base_url.rstrip("/")
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

    async def search_posts(self, query: str, candidates: List[Dict[str, Any]], limit: int = 5) -> List[Dict[str, Any]]:
        """
        Ищет публикации по смысловому описанию.
        Если доступен векторный сервер LM Studio — ранжирует по косинусному сходству.
        В ином случае — использует плотный семантический скоринг.
        """
        if not candidates:
            return []

        query_emb = await self.get_embedding(query)
        if query_emb:
            scored = []
            for it in candidates:
                text_sample = f"{it.get('title', '')} {it.get('content', '')[:600]}"
                it_emb = await self.get_embedding(text_sample)
                if it_emb:
                    sim = self.cosine_similarity(query_emb, it_emb)
                    scored.append((sim, it))
                else:
                    scored.append((0.0, it))
            scored.sort(key=lambda x: x[0], reverse=True)
            return [it for _, it in scored[:limit]]

        # Фоллбэк: семантический поиск по стеммам и ключевым концептам
        words = re.findall(r'[а-яa-z0-9_-]+', query.lower())
        stop_words = {"что", "как", "где", "когда", "найди", "пост", "канале", "видео", "вроде", "помню", "знаю", "про", "для", "или", "это"}
        stems = []
        for w in words:
            if len(w) <= 2 or w in stop_words:
                continue
            stem = re.sub(r'(?:овали|евали|ован|еван|али|яли|или|ила|ала|ели|ать|ять|ить|еть|ого|его|ому|ему|ыми|ими|ами|ями|ах|ях|ия|ие|ые|ое|ее|ом|ем|ой|ей|ам|ям|а|я|о|е|у|ю|ы|и)$', '', w)
            stems.append(stem if len(stem) >= 3 else w)
        stems = list(dict.fromkeys(stems))

        scored = []
        for it in candidates:
            corpus = f"{it.get('channel', '')} {it.get('title', '')} {it.get('content', '')}".lower()
            score = 0
            for st in stems:
                if st in corpus:
                    score += 25
            if score > 0:
                scored.append((score, it))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [it for _, it in scored[:limit]]
