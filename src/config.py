import os
from pathlib import Path
from typing import List, Dict, Any, Optional
import yaml
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent.parent

class LocalLLMConfig(BaseModel):
    enabled: bool = True
    base_url: str = "http://127.0.0.1:11434/v1"
    model: str = "qwen2.5:7b-instruct-q4_K_M"
    api_key: str = "ollama"

class CloudLLMConfig(BaseModel):
    enabled: bool = True
    api_key: str = ""
    model: str = ""
    base_url: Optional[str] = None

class LLMConfig(BaseModel):
    default_provider: str = "auto"
    timeout_seconds: int = 60
    local: LocalLLMConfig = Field(default_factory=LocalLLMConfig)
    groq: CloudLLMConfig = Field(default_factory=CloudLLMConfig)
    gemini: CloudLLMConfig = Field(default_factory=CloudLLMConfig)
    openrouter: CloudLLMConfig = Field(default_factory=CloudLLMConfig)

class BotConfig(BaseModel):
    token: str = ""
    admin_id: int = 0

class StorageConfig(BaseModel):
    db_path: str = "data/bot_database.db"

class SchedulerConfig(BaseModel):
    digest_cron: str = "50 8 * * *" # 08:50 daily
    fetch_interval_minutes: int = 30

class AppConfig(BaseModel):
    bot: BotConfig = Field(default_factory=BotConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)

def load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def load_app_config() -> AppConfig:
    config_path = BASE_DIR / "config" / "config.yaml"
    if not config_path.exists():
        config_path = BASE_DIR / "config" / "config.example.yaml"
    data = load_yaml(config_path)

    # Environment variables override
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    admin_id = os.getenv("TELEGRAM_ADMIN_ID")
    groq_key = os.getenv("GROQ_API_KEY")
    gemini_key = os.getenv("GEMINI_API_KEY")
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    local_url = os.getenv("LOCAL_QWEN_URL")

    cfg = AppConfig(**data) if data else AppConfig()
    if token:
        cfg.bot.token = token
    if admin_id and admin_id.isdigit():
        cfg.bot.admin_id = int(admin_id)
    if groq_key:
        cfg.llm.groq.api_key = groq_key
    if gemini_key:
        cfg.llm.gemini.api_key = gemini_key
    if openrouter_key:
        cfg.llm.openrouter.api_key = openrouter_key
    if local_url:
        cfg.llm.local.base_url = local_url

    return cfg

def load_preferences() -> Dict[str, Any]:
    pref_path = BASE_DIR / "config" / "preferences.yaml"
    if not pref_path.exists():
        pref_path = BASE_DIR / "config" / "preferences.example.yaml"
    return load_yaml(pref_path)

def load_sources() -> Dict[str, Any]:
    sources_path = BASE_DIR / "config" / "sources.yaml"
    if not sources_path.exists():
        sources_path = BASE_DIR / "config" / "sources.example.yaml"
    return load_yaml(sources_path)

def load_prompts() -> Dict[str, Any]:
    prompts_path = BASE_DIR / "config" / "prompts.yaml"
    if not prompts_path.exists():
        prompts_path = BASE_DIR / "config" / "prompts.example.yaml"
    return load_yaml(prompts_path)

