import os
import re
import urllib.parse
from pathlib import Path
from typing import Dict, Any, List, Optional
import yaml

BASE_DIR = Path(__file__).resolve().parent.parent.parent

class TrustManager:
    """
    Менеджер доверенных источников и реестров («Траствики»):
    1. Верифицирует .onion скрытые сервисы сети Tor по криптографическим реестрам (OnionTree, Daunt).
       Отсеивает потенциальный фишинг и вредоносные зеркала-ловушки.
    2. Проверяет источники кибербезопасности и эксплойтов (CVE Mitre, Exploit-DB, GitHub Advisories).
    3. Верифицирует OSINT-источники по СВО и геополитике (LostArmour, объективный контроль с фото/видеофиксацией).
    4. Обогащает результаты поиска бейджами доверия:
       - 🟢 [Траствик / Verified] — проверенный источник из реестра.
       - 🟡 [Авторитетный Web] — профильное издание / СМИ.
       - ⚠️ [Неверифицированный Onion] — скрытый сервис без PGP-подписи / риск фишинга.
    """

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or (BASE_DIR / "config" / "trusted_sources.yaml")
        if not self.config_path.exists():
            self.config_path = BASE_DIR / "config" / "trusted_sources.example.yaml"
        self.data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            return {}
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception:
            return {}

    def verify_url(self, url: str) -> Dict[str, Any]:
        """
        Проверяет URL на соответствие реестрам доверия (траствикам).
        Возвращает детальный отчет о статусе верификации.
        """
        if not url:
            return {
                "verified": False,
                "trust_level": "unknown",
                "badge": "⚪️ [Неизвестный источник]",
                "name": "",
                "category": "",
                "warning": ""
            }

        parsed = urllib.parse.urlparse(url)
        netloc = parsed.netloc.lower()
        full_url = url.lower()
        is_onion = ".onion" in netloc or ".onion" in full_url

        # 1. Проверка по реестру верифицированных Onion-сервисов
        if is_onion:
            dark_list = self.data.get("darknet_trusted_registries", [])
            for reg in dark_list:
                onion_sig = (reg.get("onion") or "").lower()
                domain_sig = (reg.get("domain") or "").lower()
                name = reg.get("name", "Доверенный скрытый сервис")
                cat = reg.get("category", "Верифицированный каталог")

                if onion_sig and onion_sig in full_url:
                    return {
                        "verified": True,
                        "trust_level": "verified_onion",
                        "badge": "🟢 [Траствик / Verified Onion]",
                        "name": name,
                        "category": cat,
                        "warning": ""
                    }
                if domain_sig and domain_sig in netloc:
                    return {
                        "verified": True,
                        "trust_level": "verified_onion_mirror",
                        "badge": "🟢 [Траствик / Onion Registry]",
                        "name": name,
                        "category": cat,
                        "warning": ""
                    }

            # Не найден в базе доверенных PGP-зеркал
            return {
                "verified": False,
                "trust_level": "unverified_onion",
                "badge": "⚠️ [Неверифицированный Onion / Риск фишинга]",
                "name": "Неизвестный узел Tor",
                "category": "Скрытый сервис",
                "warning": "Ресурс отсутствует в PGP-реестрах OnionTree / Daunt. Возможен фишинг или скам-зеркало."
            }

        # 2. Проверка по базам киберразведки и CVE
        sec_list = self.data.get("cybersecurity_and_exploits", [])
        for item in sec_list:
            dom = (item.get("domain") or "").lower()
            if dom and dom in netloc:
                name = item.get("name", "Репозиторий безопасности")
                cat = item.get("category", "Киберразведка")
                t_level = item.get("trust", "verified")
                badge = "🟢 [Траствик / CVE & Sec]" if t_level == "verified" else "🟡 [Профильный IT-источник]"
                return {
                    "verified": True,
                    "trust_level": t_level,
                    "badge": badge,
                    "name": name,
                    "category": cat,
                    "warning": ""
                }

        # 3. Проверка по базам объективного контроля OSINT (СВО / геополитика)
        osint_list = self.data.get("osint_and_factchecking", [])
        for item in osint_list:
            dom = (item.get("domain") or "").lower()
            if dom and dom in netloc:
                name = item.get("name", "База объективного контроля")
                cat = item.get("category", "Фактчекинг / Геолокация")
                t_level = item.get("trust", "verified")
                badge = "🟢 [Траствик / Объективный контроль]" if t_level == "verified" else "🟡 [Авторитетный OSINT]"
                return {
                    "verified": True,
                    "trust_level": t_level,
                    "badge": badge,
                    "name": name,
                    "category": cat,
                    "warning": ""
                }

        # Обычный внешний Web-сайт
        return {
            "verified": False,
            "trust_level": "web",
            "badge": "🌐 [Открытый Web]",
            "name": netloc,
            "category": "Внешняя публикация",
            "warning": ""
        }

    def enrich_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Обогащает каждый найденный результат метаданными доверия"""
        enriched = []
        for it in items:
            it_copy = dict(it)
            url = it_copy.get("url", "")
            verification = self.verify_url(url)
            it_copy["trust_verified"] = verification["verified"]
            it_copy["trust_badge"] = verification["badge"]
            it_copy["trust_level"] = verification["trust_level"]
            it_copy["trust_category"] = verification["category"]
            it_copy["trust_name"] = verification["name"]
            it_copy["trust_warning"] = verification["warning"]
            enriched.append(it_copy)
        return enriched
