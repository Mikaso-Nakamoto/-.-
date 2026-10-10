import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from src.pipeline.storage import Storage
from src.llm.router import LLMRouter, POPULAR_MODELS
from src.pipeline.digest_builder import DigestBuilder
from src.config import load_sources

logger = logging.getLogger(__name__)

class ChatRequest(BaseModel):
    message: str
    provider: Optional[str] = None
    model: Optional[str] = None

class FeedbackRequest(BaseModel):
    reaction: str # like / dislike
    context: str

def create_app(storage: Storage, router: LLMRouter, digest_builder: DigestBuilder) -> FastAPI:
    app = FastAPI(title="AI News Hub API", version="2026.1")

    # Разрешаем CORS для подключения Android приложения или браузера
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    static_dir = Path(__file__).resolve().parent.parent / "web" / "static"

    # --------------------------------------------------------------------------
    # 1. Лента новостей в стиле Google Discover
    # --------------------------------------------------------------------------
    @app.get("/api/feed")
    async def get_news_feed(limit: int = 30):
        items = storage.get_news_feed(limit=limit)
        return {
            "success": True,
            "count": len(items),
            "items": items
        }

    # --------------------------------------------------------------------------
    # 2. Последний утренний дайджест
    # --------------------------------------------------------------------------
    @app.get("/api/digest/latest")
    async def get_latest_digest():
        full_dig = storage.get_latest_full_digest()
        if full_dig:
            return {
                "success": True,
                "digest": full_dig.get("post_html") or full_dig.get("report_md"),
                "full_digest": full_dig
            }
        legacy_digest = storage.get_latest_digest()
        return {
            "success": True,
            "digest": legacy_digest or "Сводка еще не формировалась."
        }

    @app.get("/digest")
    async def view_latest_digest_html():
        full_dig = storage.get_latest_full_digest()
        if full_dig and full_dig.get("report_html_path"):
            p = Path(full_dig["report_html_path"])
            if p.exists():
                return FileResponse(str(p), media_type="text/html")
        return FileResponse(str(static_dir / "index.html"))

    @app.get("/api/digest/download/{filename}")
    async def download_digest_file(filename: str):
        safe_filename = Path(filename).name
        file_path = Path("data/digests") / safe_filename
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Файл не найден")
        return FileResponse(str(file_path), filename=safe_filename)

    # --------------------------------------------------------------------------
    # 3. Принудительный сбор свежих новостей
    # --------------------------------------------------------------------------
    @app.post("/api/digest/refresh")
    async def refresh_digest():
        res = await digest_builder.generate_digest()
        return res

    # --------------------------------------------------------------------------
    # 4. Вкладка "ИИ Чат"
    # --------------------------------------------------------------------------
    @app.post("/api/chat")
    async def chat_with_ai(req: ChatRequest):
        if not req.message.strip():
            raise HTTPException(status_code=400, detail="Текст сообщения не может быть пустым")

        # Опционально временное переопределение модели
        old_prov = router.active_provider
        if req.provider:
            router.set_provider(req.provider)
        if req.model and req.provider:
            router.set_model(req.provider, req.model)

        history = storage.get_chat_history(limit=6)
        latest_digest = storage.get_latest_digest()

        try:
            res = await router.generate_response(
                task="chat",
                user_prompt=req.message,
                chat_history=history,
                latest_digest=latest_digest
            )

            if res.get("success"):
                storage.add_chat_message("user", req.message)
                storage.add_chat_message("assistant", res.get("content", ""))

            return res
        finally:
            if req.provider:
                router.set_provider(old_prov)

    # --------------------------------------------------------------------------
    # 5. Реакции и обратная связь (Обучение предпочтений)
    # --------------------------------------------------------------------------
    @app.post("/api/feedback")
    async def record_feedback(req: FeedbackRequest):
        storage.record_feedback(req.reaction, req.context)
        return {"success": True, "message": "Реакция учтена в предпочтениях"}

    # --------------------------------------------------------------------------
    # 6. Статус узлов и модели
    # --------------------------------------------------------------------------
    @app.get("/api/status")
    async def get_system_status():
        is_local_on = await router.check_local_health()
        return {
            "local_pc_online": is_local_on,
            "active_provider": router.active_provider,
            "current_model": router.get_current_model_for_provider(router.active_provider if router.active_provider != "auto" else "openrouter"),
            "available_models": POPULAR_MODELS
        }

    # --------------------------------------------------------------------------
    # 7. Раздача мобильного PWA интерфейса
    # --------------------------------------------------------------------------
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/")
        async def serve_index():
            return FileResponse(str(static_dir / "index.html"))

    return app
