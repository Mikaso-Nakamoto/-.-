import asyncio
import html
import json
import logging
import os
import re
import shutil
import socket
import urllib.parse
from typing import Dict, Any, List, Optional, Tuple

import httpx

from src.llm.router import LLMRouter
from src.pipeline.trust_manager import TrustManager

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"


class OSINTToolkit:
    """
    Автономный модульный комплекс OSINT-разведки (по стандартам OSINT Framework / osintframework.com).
    Выполняет разведку по открытым источникам (OSINT) локально на машине пользователя:
    1. Сетевая инфраструктура и домены (DNS, RDAP, субдомены crt.sh/subfinder, HTTP-стек, открытые порты).
    2. IP-адреса и сетевые узлы (Reverse DNS, GeoIP, ASN, nmap/сканирование портов).
    3. Никнеймы и цифровой след (многоплатформенный чекер аккаунтов Maigret / Sherlock style).
    4. Почты и Телефоны (Holehe 120+ сервисов, phonenumbers).
    5. Архивы и история изменений (Wayback Machine).
    6. Автоматическая генерация Google/Yandex Dorks.
    7. Криптовалютные кошельки (BTC, ETH, TRC-20 USDT).
    8. Уязвимости CVE и Cyber Threat Intelligence (CTI).
    9. Интегрированный справочник специализированных инструментов OSINT Framework.
    """

    def __init__(self, trust_manager: Optional[TrustManager] = None):
        self.trust_manager = trust_manager or TrustManager()
        self.xray_http_proxy = os.getenv("HTTP_PROXY") or os.getenv("WEB_SEARCH_PROXY") or "http://127.0.0.1:10809"

    def detect_entity_type(self, target: str) -> str:
        """Определяет тип входной цели для запуска нужного модуля разведки"""
        t = target.strip()
        # IP
        if re.match(r'^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$', t):
            return "ip"
        # CVE
        if re.match(r'^CVE-\d{4}-\d+$', t, re.IGNORECASE):
            return "cve"
        # Криптовалютный кошелек
        if re.match(r'^(?:0x[a-fA-F0-9]{40}|(?:1|3|bc1)[a-zA-HJ-NP-Z0-9]{25,59}|T[A-Za-z1-9]{33})$', t):
            return "crypto"
        # Email
        if re.match(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$', t):
            return "email"
        # Телефон
        if re.match(r'^\+?[1-9]\d{7,14}$', t):
            return "phone"
        # Домен (если есть точка и нет пробелов)
        if re.match(r'^(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}$', t) and ' ' not in t:
            return "domain"
        # Никнейм / хэндл (одно слово из 3-32 символов)
        if re.match(r'^[a-zA-Z0-9_.-]{3,32}$', t) and not t.isdigit():
            return "username"
        return "general"

    # =========================================================================
    # 1. Разведка доменов и инфраструктуры (Domain OSINT)
    # =========================================================================
    async def investigate_domain(self, domain: str, timeout: float = 8.0) -> Dict[str, Any]:
        """Комплексная разведка домена: DNS, субдомены, RDAP/WHOIS, HTTP-стек, Wayback"""
        domain = domain.lower().strip().replace("https://", "").replace("http://", "").split("/")[0]
        data: Dict[str, Any] = {
            "entity": domain,
            "type": "domain",
            "ips": [],
            "dns_records": {},
            "subdomains": [],
            "rdap": {},
            "http_headers": {},
            "wayback": {},
            "dorks": self.generate_dorks(domain)
        }

        # 1. DNS A-записи через локальный сокет
        try:
            _, _, addrs = socket.gethostbyname_ex(domain)
            data["ips"] = list(dict.fromkeys(addrs))
        except Exception:
            pass

        # 2. DNS DoH (Cloudflare / Google) для MX, TXT, NS
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            for r_type in ["MX", "TXT", "NS"]:
                try:
                    doh_url = f"https://dns.google/resolve?name={domain}&type={r_type}"
                    resp = await client.get(doh_url)
                    if resp.status_code == 200:
                        answers = resp.json().get("Answer", [])
                        data["dns_records"][r_type] = [a.get("data") for a in answers if "data" in a][:5]
                except Exception:
                    pass

            # 3. Субдомены через Subfinder (если установлен) или Certificate Transparency (crt.sh)
            subfinder_bin = shutil.which("subfinder")
            if subfinder_bin:
                try:
                    proc = await asyncio.create_subprocess_exec(
                        subfinder_bin, "-d", domain, "-silent",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL
                    )
                    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=12.0)
                    if stdout:
                        for s in stdout.decode().splitlines():
                            s = s.strip()
                            if s and s != domain and s not in data["subdomains"]:
                                data["subdomains"].append(s)
                except Exception:
                    pass

            if not data["subdomains"]:
                try:
                    crt_url = f"https://crt.sh/?q=%25.{domain}&output=json"
                    crt_resp = await client.get(crt_url)
                    if crt_resp.status_code == 200:
                        entries = crt_resp.json()
                        subdomains = set()
                        for entry in entries[:50]:
                            name_val = entry.get("name_value", "")
                            for sub in name_val.split("\n"):
                                sub = sub.strip().lower()
                                if sub and "*" not in sub and sub != domain:
                                    subdomains.add(sub)
                        data["subdomains"] = sorted(list(subdomains))[:15]
                except Exception:
                    pass

            # 4. RDAP (WHOIS нового поколения)
            try:
                rdap_url = f"https://rdap.org/domain/{domain}"
                rdap_resp = await client.get(rdap_url)
                if rdap_resp.status_code == 200:
                    rd = rdap_resp.json()
                    events = {ev.get("eventAction"): ev.get("eventDate") for ev in rd.get("events", [])}
                    data["rdap"] = {
                        "name": rd.get("ldhName"),
                        "registrar": rd.get("entities", [{}])[0].get("vcardArray", [None, [None, None, None, [""]]])[1][3][3] if rd.get("entities") else "Не указан",
                        "created": events.get("registration", "Н/Д"),
                        "expires": events.get("expiration", "Н/Д"),
                        "status": rd.get("status", [])[:3]
                    }
            except Exception:
                pass

            # 5. HTTP Tech-стек
            try:
                http_resp = await client.get(f"https://{domain}")
                headers = http_resp.headers
                data["http_headers"] = {
                    "status_code": http_resp.status_code,
                    "server": headers.get("server", "Не указан"),
                    "x_powered_by": headers.get("x-powered-by"),
                    "hsts": bool(headers.get("strict-transport-security")),
                    "title": re.search(r'<title>(.*?)</title>', http_resp.text, re.IGNORECASE).group(1).strip() if re.search(r'<title>(.*?)</title>', http_resp.text, re.IGNORECASE) else ""
                }
            except Exception:
                pass

            # 6. Wayback Machine (archive.org)
            try:
                wb_url = f"https://archive.org/wayback/available?url={domain}"
                wb_resp = await client.get(wb_url)
                if wb_resp.status_code == 200:
                    snapshots = wb_resp.json().get("archived_snapshots", {})
                    closest = snapshots.get("closest", {})
                    if closest.get("available"):
                        data["wayback"] = {
                            "timestamp": closest.get("timestamp"),
                            "url": closest.get("url")
                        }
            except Exception:
                pass

        return data

    # =========================================================================
    # 2. Разведка IP-адресов и сетевых узлов (IP OSINT)
    # =========================================================================
    async def investigate_ip(self, ip: str, timeout: float = 6.0) -> Dict[str, Any]:
        """Комплексная разведка IP-адреса: GeoIP, ASN, PTR, nmap/сканирование ключевых портов"""
        ip = ip.strip()
        data: Dict[str, Any] = {
            "entity": ip,
            "type": "ip",
            "hostname": None,
            "geoip": {},
            "open_ports": [],
            "nmap_services": [],
            "reputation_links": {
                "AbuseIPDB": f"https://www.abuseipdb.com/check/{ip}",
                "Shodan": f"https://www.shodan.io/host/{ip}",
                "Censys": f"https://search.censys.io/hosts/{ip}",
                "VirusTotal": f"https://www.virustotal.com/gui/ip-address/{ip}"
            }
        }

        # 1. Reverse DNS (PTR)
        try:
            host, _, _ = socket.gethostbyaddr(ip)
            data["hostname"] = host
        except Exception:
            pass

        # 2. GeoIP & ASN (открытый API)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                r = await client.get(f"http://ip-api.com/json/{ip}?fields=status,message,country,countryCode,regionName,city,isp,org,as,query")
                if r.status_code == 200:
                    g_data = r.json()
                    if g_data.get("status") == "success":
                        data["geoip"] = {
                            "country": g_data.get("country"),
                            "city": g_data.get("city"),
                            "region": g_data.get("regionName"),
                            "isp": g_data.get("isp"),
                            "org": g_data.get("org"),
                            "asn": g_data.get("as")
                        }
            except Exception:
                pass

        # 3. Nmap (если установлен) или быстрый асинхронный сокет-скан
        nmap_bin = shutil.which("nmap")
        if nmap_bin:
            try:
                proc = await asyncio.create_subprocess_exec(
                    nmap_bin, "-sV", "--open", "-T4", "-F", ip,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=12.0)
                if stdout:
                    lines = stdout.decode().splitlines()
                    for line in lines:
                        m = re.match(r'^(\d+)/(tcp|udp)\s+open\s+(\S+)\s*(.*)', line)
                        if m:
                            port_num = int(m.group(1))
                            service = m.group(3)
                            ver = m.group(4).strip()
                            data["nmap_services"].append({"port": port_num, "service": service, "version": ver})
                            if port_num not in data["open_ports"]:
                                data["open_ports"].append(port_num)
            except Exception:
                pass

        if not data["open_ports"]:
            target_ports = [21, 22, 25, 53, 80, 443, 3306, 3389, 8080]
            async def check_port(port: int):
                try:
                    conn = asyncio.open_connection(ip, port)
                    _, writer = await asyncio.wait_for(conn, timeout=1.2)
                    writer.close()
                    await writer.wait_closed()
                    return port
                except Exception:
                    return None

            tasks = [check_port(p) for p in target_ports]
            port_results = await asyncio.gather(*tasks, return_exceptions=True)
            data["open_ports"] = [p for p in port_results if isinstance(p, int)]

        return data

    # =========================================================================
    # 3. Разведка никнеймов и цифрового следа (Username OSINT / Maigret / Sherlock)
    # =========================================================================
    async def investigate_username(self, username: str, timeout: float = 5.0) -> Dict[str, Any]:
        """Проверка присутствия никнейма по Maigret (если установлен) или по 12+ популярным платформам"""
        username = username.strip().lstrip("@")
        found_profiles: List[Dict[str, str]] = []

        # 1. Если установлен CLI инструмент Maigret (3000+ сайтов)
        maigret_bin = shutil.which("maigret")
        if maigret_bin:
            try:
                proc = await asyncio.create_subprocess_exec(
                    maigret_bin, username, "--timeout", "5", "--top-sites", "150", "--json", "-",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=18.0)
                if stdout:
                    m_data = json.loads(stdout.decode())
                    for site_name, s_info in m_data.items():
                        if s_info.get("status") == "Claimed" and s_info.get("url_user"):
                            found_profiles.append({"platform": site_name, "url": s_info["url_user"]})
            except Exception as me:
                logger.debug(f"Maigret execution failed or timed out: {me}")

        # 2. Встроенный быстрый зонд по ключевым сетям
        if not found_profiles:
            platforms = [
                ("GitHub", f"https://api.github.com/users/{username}", f"https://github.com/{username}"),
                ("Telegram", f"https://t.me/{username}", f"https://t.me/{username}"),
                ("VKontakte", f"https://vk.com/{username}", f"https://vk.com/{username}"),
                ("Reddit", f"https://www.reddit.com/user/{username}/about.json", f"https://www.reddit.com/user/{username}"),
                ("Steam", f"https://steamcommunity.com/id/{username}", f"https://steamcommunity.com/id/{username}"),
                ("Habr", f"https://habr.com/ru/users/{username}/", f"https://habr.com/ru/users/{username}/"),
                ("Docker Hub", f"https://hub.docker.com/v2/users/{username}", f"https://hub.docker.com/u/{username}"),
                ("Pastebin", f"https://pastebin.com/u/{username}", f"https://pastebin.com/u/{username}"),
                ("Keybase", f"https://keybase.io/{username}", f"https://keybase.io/{username}"),
                ("GitLab", f"https://gitlab.com/{username}", f"https://gitlab.com/{username}"),
                ("Medium", f"https://medium.com/@{username}", f"https://medium.com/@{username}"),
            ]

            headers = {"User-Agent": USER_AGENT}
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
                async def probe(name: str, check_url: str, profile_url: str):
                    try:
                        r = await client.get(check_url)
                        if r.status_code == 200:
                            if name == "Telegram" and "tgme_page_title" not in r.text:
                                return None
                            if name == "Steam" and "The specified profile could not be found" in r.text:
                                return None
                            if name == "VKontakte" and ("Страница удалена" in r.text or "Страница заблокирована" in r.text):
                                return None
                            if name == "Reddit" and r.json().get("error") == 404:
                                return None
                            return {"platform": name, "url": profile_url}
                    except Exception:
                        return None

                tasks = [probe(p[0], p[1], p[2]) for p in platforms]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for it in results:
                    if isinstance(it, dict) and it:
                        found_profiles.append(it)

        return {
            "entity": username,
            "type": "username",
            "found_count": len(found_profiles),
            "profiles": found_profiles,
            "search_dorks": [
                f'"{username}" site:github.com OR site:gitlab.com',
                f'"{username}" site:t.me OR site:vk.com',
                f'intext:"{username}" filetype:pdf OR filetype:txt'
            ]
        }

    # =========================================================================
    # 4. Разведка по Email (Holehe 120+ сервисов и MX-записи)
    # =========================================================================
    async def investigate_email(self, email: str, timeout: float = 8.0) -> Dict[str, Any]:
        """Разведка по Email: проверка привязки к 120+ сервисам через Holehe и MX-записи"""
        email = email.strip().lower()
        domain = email.split("@")[-1] if "@" in email else ""
        data: Dict[str, Any] = {
            "entity": email,
            "type": "email",
            "domain": domain,
            "registered_services": [],
            "mx_records": [],
            "breach_search_links": [
                f"https://haveibeenpwned.com/account/{urllib.parse.quote(email)}",
                f"https://leakcheck.io/search?type=email&query={urllib.parse.quote(email)}",
                f"https://dehashed.com/search?query={urllib.parse.quote(email)}"
            ]
        }

        # 1. Если установлен CLI инструмент Holehe (120+ сервисов)
        holehe_bin = shutil.which("holehe")
        if holehe_bin:
            try:
                proc = await asyncio.create_subprocess_exec(
                    holehe_bin, email, "--only-used", "--no-color",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=18.0)
                if stdout:
                    for line in stdout.decode().splitlines():
                        if "[+]" in line:
                            clean_svc = re.sub(r'\[\+\]\s*', '', line).strip()
                            data["registered_services"].append(clean_svc)
            except Exception as he:
                logger.debug(f"Holehe execution error: {he}")

        # 2. DNS MX records для домена почты
        if domain:
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    doh_url = f"https://dns.google/resolve?name={domain}&type=MX"
                    r = await client.get(doh_url)
                    if r.status_code == 200:
                        answers = r.json().get("Answer", [])
                        data["mx_records"] = [a.get("data") for a in answers if "data" in a][:4]
            except Exception:
                pass

        return data

    # =========================================================================
    # 5. Разведка по номеру телефона (Phone OSINT / phonenumbers)
    # =========================================================================
    async def investigate_phone(self, phone: str) -> Dict[str, Any]:
        """Разведка по номеру телефона: форматирование, страна, оператор, ссылки на проверку"""
        clean_phone = re.sub(r'[^\d+]', '', phone.strip())
        if not clean_phone.startswith("+"):
            if clean_phone.startswith("8") and len(clean_phone) == 11:
                clean_phone = "+7" + clean_phone[1:]
            elif clean_phone.startswith("7") and len(clean_phone) == 11:
                clean_phone = "+" + clean_phone

        data: Dict[str, Any] = {
            "entity": clean_phone,
            "type": "phone",
            "country": None,
            "carrier": None,
            "valid": True,
            "search_links": [
                f"https://t.me/+{clean_phone.lstrip('+')}",
                f"https://api.whatsapp.com/send?phone={clean_phone.lstrip('+')}",
                f"https://www.google.com/search?q=\"{urllib.parse.quote(clean_phone)}\"",
                f"https://yandex.ru/search/?text=\"{urllib.parse.quote(clean_phone)}\"",
                f"https://sync.me/search/?number={clean_phone.lstrip('+')}"
            ]
        }

        try:
            import phonenumbers
            from phonenumbers import geocoder, carrier
            parsed = phonenumbers.parse(clean_phone, None)
            data["valid"] = phonenumbers.is_valid_number(parsed)
            data["country"] = geocoder.description_for_number(parsed, "ru") or geocoder.description_for_number(parsed, "en")
            data["carrier"] = carrier.name_for_number(parsed, "ru") or carrier.name_for_number(parsed, "en")
        except Exception:
            pass

        return data

    # =========================================================================
    # 6. Разведка уязвимостей CVE (Cyber Threat Intelligence)
    # =========================================================================
    async def investigate_cve(self, cve_id: str, timeout: float = 6.0) -> Dict[str, Any]:
        """Поиск характеристик уязвимости CVE и эксплойтов"""
        cve_id = cve_id.strip().upper()
        data = {
            "entity": cve_id,
            "type": "cve",
            "description": None,
            "cvss_score": None,
            "severity": None,
            "exploit_db_link": f"https://www.exploit-db.com/search?cve={cve_id.replace('CVE-', '')}",
            "nvd_link": f"https://nvd.nist.gov/vuln/detail/{cve_id}"
        }

        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                api_url = f"https://services.nvd.nist.gov/rest/json/cves/2.0?cveId={cve_id}"
                r = await client.get(api_url)
                if r.status_code == 200:
                    vulns = r.json().get("vulnerabilities", [])
                    if vulns:
                        cve_item = vulns[0].get("cve", {})
                        descs = cve_item.get("descriptions", [])
                        for d in descs:
                            if d.get("lang") == "en":
                                data["description"] = d.get("value")
                                break
                        metrics = cve_item.get("metrics", {})
                        for m_ver in ["cvssMetricV31", "cvssMetricV30", "cvssMetricV2"]:
                            if m_ver in metrics and metrics[m_ver]:
                                cvss_data = metrics[m_ver][0].get("cvssData", {})
                                data["cvss_score"] = cvss_data.get("baseScore")
                                data["severity"] = cvss_data.get("baseSeverity") or metrics[m_ver][0].get("baseSeverity")
                                break
            except Exception:
                pass

        return data

    # =========================================================================
    # 7. Разведка криптовалютных адресов (Blockchain OSINT)
    # =========================================================================
    def investigate_crypto(self, address: str) -> Dict[str, Any]:
        """Анализ блокчейн-адреса и привязка к эксплорерам"""
        addr = address.strip()
        coin = "Unknown"
        explorers = []

        if addr.startswith("0x") and len(addr) == 42:
            coin = "Ethereum / EVM"
            explorers = [
                f"https://etherscan.io/address/{addr}",
                f"https://debank.com/profile/{addr}",
                f"https://blockscan.com/address/{addr}"
            ]
        elif addr.startswith(("1", "3", "bc1")):
            coin = "Bitcoin"
            explorers = [
                f"https://mempool.space/address/{addr}",
                f"https://www.blockchain.com/explorer/addresses/btc/{addr}",
                f"https://blockchair.com/bitcoin/address/{addr}"
            ]
        elif addr.startswith("T") and len(addr) == 34:
            coin = "TRON / USDT TRC-20"
            explorers = [
                f"https://tronscan.org/#/address/{addr}",
                f"https://oklink.com/tron/address/{addr}"
            ]

        return {
            "entity": addr,
            "type": "crypto",
            "blockchain": coin,
            "explorers": explorers
        }

    # =========================================================================
    # 8. Генератор профессиональных Google / Yandex Dorks
    # =========================================================================
    @staticmethod
    def generate_dorks(target: str) -> List[Dict[str, str]]:
        """Формирует готовые поисковые дорки по стандартам OSINT Framework"""
        clean = target.strip().replace("https://", "").replace("http://", "").split("/")[0]
        return [
            {
                "category": "Утечки конфигураций и ключей",
                "dork": f'site:{clean} ext:env OR ext:yml OR ext:yaml OR ext:config OR ext:sql'
            },
            {
                "category": "Служебные и административные панели",
                "dork": f'site:{clean} inurl:admin OR inurl:login OR inurl:dashboard OR inurl:portal'
            },
            {
                "category": "Публичные документы и отчеты",
                "dork": f'site:{clean} filetype:pdf OR filetype:docx OR filetype:xlsx'
            },
            {
                "category": "Индексированные посты в Telegram",
                "dork": f'site:t.me/s/ "{clean}"'
            },
            {
                "category": "Скрытые субдомены",
                "dork": f'site:*.{clean} -www.{clean}'
            }
        ]

    # =========================================================================
    # 9. Каталог инструментов OSINT Framework (Knowledge Directory)
    # =========================================================================
    @staticmethod
    def get_framework_tools(category: Optional[str] = None) -> List[Dict[str, Any]]:
        """Возвращает каталог проверенных инструментов OSINT Framework по категориям"""
        catalog = [
            {
                "category": "GEOINT (Спутники, карты, военная геолокация)",
                "tools": [
                    {"name": "Sentinel Hub / Copernicus", "desc": "Бесплатные свежие спутниковые снимки высокого разрешения ESA", "url": "https://browser.dataspace.copernicus.eu/"},
                    {"name": "Wikimapia", "desc": "Краудсорсинговая разметка военных и промышленных объектов по всему миру", "url": "https://wikimapia.org/"},
                    {"name": "Liveuamap", "desc": "Интерактивная карта боевых действий, инцидентов и геополитики", "url": "https://liveuamap.com/"},
                    {"name": "Flightradar24 / ADS-B Exchange", "desc": "Радиолокационное отслеживание военной и разведывательной авиации", "url": "https://www.adsbexchange.com/"},
                    {"name": "MarineTraffic", "desc": "Слежение за морскими судами, танкерами и военными конвоями через AIS", "url": "https://www.marinetraffic.com/"},
                    {"name": "SunCalc", "desc": "Анализ времени суток по теням для точной хронолокации фото/видео", "url": "https://www.suncalc.org/"}
                ]
            },
            {
                "category": "Dark Web & Tor (Теневой сегмент)",
                "tools": [
                    {"name": "Ahmia.fi", "desc": "Поисковая система по индексированным скрытым сервисам сети Tor (.onion)", "url": "https://ahmia.fi/"},
                    {"name": "OnionTree", "desc": "Репозиторий верифицированных зеркал и PGP-ключей теневых ресурсов", "url": "https://oniontree.org/"},
                    {"name": "Dark.fail", "desc": "Мониторинг доступности и проверка зеркал onion-сервисов", "url": "https://dark.fail/"}
                ]
            },
            {
                "category": "Cyber Threat Intelligence (Киберразведка и уязвимости)",
                "tools": [
                    {"name": "VirusTotal", "desc": "Анализ файлов, хешей, доменов и IP по базам 70+ антивирусных вендоров", "url": "https://www.virustotal.com/"},
                    {"name": "AbuseIPDB", "desc": "Глобальная база данных вредоносных IP-адресов, ботнетов и спама", "url": "https://www.abuseipdb.com/"},
                    {"name": "Shodan.io", "desc": "Поисковик по подключенным к сети серверам, камерам, роутерам и портам", "url": "https://www.shodan.io/"},
                    {"name": "Censys", "desc": "Глубокое сканирование сертификатов, хостов и интернет-инфраструктуры", "url": "https://search.censys.io/"},
                    {"name": "Exploit-DB", "desc": "Официальный архив готовых эксплойтов и PoC для известных CVE", "url": "https://www.exploit-db.com/"}
                ]
            },
            {
                "category": "Social & People (Люди, никнеймы, социальные сети)",
                "tools": [
                    {"name": "Maigret / Sherlock", "desc": "Автоматический поиск аккаунтов по никнейму на 500+ сайтах и форумах", "url": "https://github.com/soxoj/maigret"},
                    {"name": "Holehe", "desc": "Проверка привязки Email к 120+ сервисам без уведомления цели", "url": "https://github.com/megadose/holehe"},
                    {"name": "Epieos", "desc": "Разведка по Email (Google, Skype, LinkedIn) без отправки оповещений", "url": "https://epieos.com/"},
                    {"name": "Telepathy", "desc": "OSINT по открытым Telegram-чатам, каналам и связям участников", "url": "https://github.com/jordanwildon/Telepathy"}
                ]
            },
            {
                "category": "Archives & Metadata (Архивы и цифровые отпечатки)",
                "tools": [
                    {"name": "Wayback Machine", "desc": "Архив интернета: просмотр удаленных сайтов и старых версий страниц", "url": "https://web.archive.org/"},
                    {"name": "Archive.today", "desc": "Мгновенное создание неизменяемого снапшота любой веб-страницы", "url": "https://archive.is/"},
                    {"name": "ExifTool", "desc": "Извлечение метаданных, GPS-координат и моделей камер из фото/документов", "url": "https://exiftool.org/"}
                ]
            }
        ]

        if not category:
            return catalog

        cat_low = category.lower()
        return [c for c in catalog if cat_low in c["category"].lower()]

    # =========================================================================
    # 10. Главный исполнитель разведки (Execute Investigation)
    # =========================================================================
    async def investigate(self, target: str, entity_type: Optional[str] = None) -> Dict[str, Any]:
        """Запускает разведку по определенному типу сущности"""
        clean_target = target.strip()
        etype = entity_type or self.detect_entity_type(clean_target)

        if etype == "ip":
            return await self.investigate_ip(clean_target)
        elif etype == "domain":
            return await self.investigate_domain(clean_target)
        elif etype == "username":
            return await self.investigate_username(clean_target)
        elif etype == "email":
            return await self.investigate_email(clean_target)
        elif etype == "phone":
            return await self.investigate_phone(clean_target)
        elif etype == "crypto":
            return self.investigate_crypto(clean_target)
        elif etype == "cve":
            return await self.investigate_cve(clean_target)
        else:
            return {
                "entity": clean_target,
                "type": "general",
                "dorks": self.generate_dorks(clean_target),
                "framework_tools": self.get_framework_tools()
            }

    # =========================================================================
    # 11. Интеллектуальный синтез отчета нейросетью (AI OSINT Synthesis)
    # =========================================================================
    async def synthesize_report(self, target: str, raw_data: Dict[str, Any], llm_router: Optional[LLMRouter] = None) -> str:
        """Синтезирует профессиональный OSINT-отчет с помощью локальной/облачной нейросети"""
        etype = raw_data.get("type", "general")
        raw_json_str = json.dumps(raw_data, ensure_ascii=False, indent=2)

        if not llm_router:
            return self._build_fallback_report(target, raw_data)

        system_prompt = (
            "ТЫ — ВЕДУЩИЙ ОФИЦЕР ТЕХНИЧЕСКОЙ РАЗВЕДКИ OSINT РАЗВЕДЦЕНТРА «АГЕНТ СНОУДЕН» (2026 год).\n"
            "Твоя задача — проанализировать собранную техническую телеметрию (DNS, субдомены, открытые порты, аккаунты, GeoIP, CVE) "
            "и сформировать исчерпывающее аналитическое досье по стандартам OSINT Framework.\n\n"
            "СТРУКТУРА ОТВЕТА (СТРОГО TELEGRAM HTML):\n"
            "1. 🎯 <b>OSINT-заключение и оценка рисков:</b>\n"
            "   - Краткий вывод о природе объекта, уровень критичности, кому принадлежит.\n"
            "2. 🔍 <b>Технический профиль и цифровой след:</b>\n"
            "   - Все обнаруженные факты (IP, провайдер, страна, открытые порты, субдомены или найденные профили).\n"
            "3. 🛡 <b>Векторы атак, утечки и открытые интерфейсы:</b>\n"
            "   - Где обнаружены уязвимости, экспонированные службы или следы компрометации.\n"
            "4. 📌 <b>Рекомендации по дальнейшей разведке / защите:</b>\n"
            "   - Конкретные шаги (применение dorking, Shodan, проверка утечек, закрытие портов).\n\n"
            "СТРОЖАЙШИЕ ПРАВИЛА ВЕРСТКИ:\n"
            "- Используй ТОЛЬКО разрешенный Telegram HTML (<b>, <i>, <code>, <blockquote>, <a href=\"...\">).\n"
            "- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕН Markdown (**жирный**, ```код```)!\n"
            "- Запрещено оборачивать весь текст в giant <blockquote>! Цитаты только для прямых реплик в 1-2 строки."
        )

        user_prompt = f"Цель разведки: «{target}» (Тип: {etype})\n\nСобранные технические телеметрические данные:\n{raw_json_str}"

        try:
            res = await llm_router.generate_response(
                task="deep_search",
                user_prompt=user_prompt,
                override_system_prompt=system_prompt
            )
            if res.get("success") and res.get("content"):
                return res["content"].strip()
        except Exception as e:
            logger.warning(f"Ошибка ИИ при синтезе OSINT-отчета: {e}")

        return self._build_fallback_report(target, raw_data)

    def _build_fallback_report(self, target: str, data: Dict[str, Any]) -> str:
        """Резервный фактологический генератор отчета без ИИ"""
        lines = [f"🛡 <b>OSINT-разведка по объекту:</b> <code>{html.escape(target)}</code>\n"]
        t = data.get("type")

        if t == "domain":
            lines.append(f"🌐 <b>Доменная инфраструктура:</b>")
            if data.get("ips"):
                lines.append(f"• IP-адреса: <code>{', '.join(data['ips'])}</code>")
            rd = data.get("rdap", {})
            if rd:
                lines.append(f"• Регистратор: <b>{html.escape(rd.get('registrar', 'Н/Д'))}</b>")
                lines.append(f"• Создан: <i>{rd.get('created', 'Н/Д')}</i> | Истекает: <i>{rd.get('expires', 'Н/Д')}</i>")
            subs = data.get("subdomains", [])
            if subs:
                lines.append(f"\n🔍 <b>Обнаруженные субдомены (crt.sh/subfinder):</b>\n" + "\n".join([f"• <code>{s}</code>" for s in subs[:8]]))
            dorks = data.get("dorks", [])
            if dorks:
                lines.append("\n🔎 <b>Готовые Google Dorks для цели:</b>")
                for d in dorks[:3]:
                    lines.append(f"• <i>{d['category']}:</i>\n<code>{html.escape(d['dork'])}</code>")

        elif t == "ip":
            lines.append(f"📍 <b>Сетевой узел:</b>")
            g = data.get("geoip", {})
            if g:
                lines.append(f"• Страна: <b>{g.get('country')} ({g.get('city')})</b>")
                lines.append(f"• Провайдер / ASN: <i>{g.get('isp')} | {g.get('asn')}</i>")
            if data.get("hostname"):
                lines.append(f"• Reverse DNS (PTR): <code>{html.escape(data['hostname'])}</code>")
            ports = data.get("open_ports", [])
            if ports:
                lines.append(f"• Открытые порты: <b>{', '.join(map(str, ports))}</b>")
            services = data.get("nmap_services", [])
            if services:
                lines.append("• Службы (Nmap): " + ", ".join([f"<code>{s['port']} ({s['service']})</code>" for s in services[:5]]))
            lines.append("\n🔗 <b>Внешние реестры репутации:</b>")
            for name, url in data.get("reputation_links", {}).items():
                lines.append(f"• <a href=\"{url}\">{name}</a>")

        elif t == "username":
            lines.append(f"👤 <b>Профиль цифрового следа:</b> @{html.escape(target)}")
            profiles = data.get("profiles", [])
            if profiles:
                lines.append(f"✅ Обнаружены активные аккаунты ({len(profiles)}):")
                for p in profiles:
                    lines.append(f"• <b>{p['platform']}:</b> <a href=\"{p['url']}\">{p['url']}</a>")
            else:
                lines.append("<i>Прямых открытых совпадений по базовым платформам не обнаружено.</i>")

        elif t == "email":
            lines.append(f"📧 <b>Анализ Email-адреса:</b> <code>{html.escape(target)}</code>")
            svcs = data.get("registered_services", [])
            if svcs:
                lines.append(f"✅ Обнаружены регистрации на сервисах ({len(svcs)}):\n" + "\n".join([f"• <b>{s}</b>" for s in svcs]))
            else:
                lines.append("<i>Holehe не зафиксировал открытых привязок или утилита не установлена.</i>")
            if data.get("mx_records"):
                lines.append(f"• Почтовые серверы MX: <code>{', '.join(data['mx_records'][:3])}</code>")
            lines.append("\n🔗 <b>Проверка по базам утечек:</b>")
            for b_link in data.get("breach_search_links", []):
                lines.append(f"• <a href=\"{b_link}\">{urllib.parse.urlparse(b_link).netloc}</a>")

        elif t == "phone":
            lines.append(f"📱 <b>Анализ телефонного номера:</b> <code>{html.escape(target)}</code>")
            if data.get("country"):
                lines.append(f"• Страна / Регион: <b>{data.get('country')}</b>")
            if data.get("carrier"):
                lines.append(f"• Оператор связи: <b>{data.get('carrier')}</b>")
            lines.append("\n🔗 <b>Поиск в мессенджерах и поисковиках:</b>")
            for link in data.get("search_links", []):
                lines.append(f"• <a href=\"{link}\">{urllib.parse.urlparse(link).netloc}</a>")

        elif t == "cve":
            lines.append(f"🚨 <b>Уязвимость информационной безопасности:</b>")
            lines.append(f"• CVSS Score: <b>{data.get('cvss_score') or 'Н/Д'}</b> ({data.get('severity') or 'Н/Д'})")
            if data.get("description"):
                lines.append(f"• Описание: <blockquote>{html.escape(data['description'][:300])}...</blockquote>")
            lines.append(f"• Эксплойты: <a href=\"{data['exploit_db_link']}\">Exploit-DB</a> | <a href=\"{data['nvd_link']}\">NVD NIST</a>")

        elif t == "crypto":
            lines.append(f"🪙 <b>Блокчейн-кошелек:</b> {data.get('blockchain')}")
            lines.append("🔗 <b>Обозреватели блоков:</b>")
            for u in data.get("explorers", []):
                lines.append(f"• <a href=\"{u}\">{u}</a>")

        return "\n".join(lines)
