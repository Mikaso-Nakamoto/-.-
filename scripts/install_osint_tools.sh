#!/usr/bin/env bash
# ==============================================================================
# Скрипт автоматической установки модулей и утилит OSINT Framework
# Для Linux Mint 22 / Ubuntu / Debian и окружения «Агент Сноуден»
# ==============================================================================

set -e

echo "======================================================================"
echo "🛡 Установка комплекса утилит OSINT Framework для «Агента Сноуден»"
echo "======================================================================"

# 1. Системные пакеты (apt)
echo -e "\n📦 [1/4] Обновление репозиториев и установка системных утилит..."
if command -v sudo &> /dev/null; then
    sudo apt update -qq
    sudo apt install -y -qq whois nmap libimage-exiftool-perl dnsutils curl jq tesseract-ocr tesseract-ocr-rus
else
    apt update -qq
    apt install -y -qq whois nmap libimage-exiftool-perl dnsutils curl jq tesseract-ocr tesseract-ocr-rus
fi
echo "✓ Системные утилиты (whois, nmap, exiftool, dig, tesseract) установлены."

# 2. Python OSINT библиотеки (pip)
echo -e "\n🐍 [2/4] Установка лучших Python-библиотек OSINT разведки..."
PYTHON_BIN="python3"
if [ -d "venv" ]; then
    PYTHON_BIN="venv/bin/python"
elif [ -d ".venv" ]; then
    PYTHON_BIN=".venv/bin/python"
fi

$PYTHON_BIN -m pip install --upgrade --no-cache-dir \
    phonenumbers \
    maigret \
    holehe

echo "✓ Maigret (поиск по 3000+ сайтам), Holehe (проверка 120+ почтовых сервисов) и PhoneNumbers установлены."

# 3. ProjectDiscovery Subfinder (быстрый поиск субдоменов)
echo -e "\n🔍 [3/4] Установка ProjectDiscovery Subfinder..."
if ! command -v subfinder &> /dev/null; then
    ARCH=$(uname -m)
    case "$ARCH" in
        x86_64) SUB_ARCH="amd64" ;;
        aarch64) SUB_ARCH="arm64" ;;
        *) SUB_ARCH="amd64" ;;
    esac
    
    TMP_DIR=$(mktemp -d)
    SUBFINDER_VER="2.6.8"
    SUB_URL="https://github.com/projectdiscovery/subfinder/releases/download/v${SUBFINDER_VER}/subfinder_${SUBFINDER_VER}_linux_${SUB_ARCH}.zip"
    
    echo "Загрузка Subfinder v${SUBFINDER_VER} ($SUB_ARCH)..."
    if curl -sSL "$SUB_URL" -o "$TMP_DIR/subfinder.zip"; then
        unzip -q -o "$TMP_DIR/subfinder.zip" -d "$TMP_DIR"
        if [ -f "$TMP_DIR/subfinder" ]; then
            if command -v sudo &> /dev/null; then
                sudo mv "$TMP_DIR/subfinder" /usr/local/bin/subfinder
                sudo chmod +x /usr/local/bin/subfinder
            else
                mv "$TMP_DIR/subfinder" /usr/local/bin/subfinder
                chmod +x /usr/local/bin/subfinder
            fi
            echo "✓ Subfinder успешно установлен в /usr/local/bin/subfinder"
        fi
    else
        echo "⚠️ Не удалось загрузить бинарник subfinder, будет использоваться встроенный fallback crt.sh"
    fi
    rm -rf "$TMP_DIR"
else
    echo "✓ Subfinder уже установлен: $(command -v subfinder)"
fi

# 4. Проверка установленных утилит
echo -e "\n📊 [4/4] Сводная диагностика установленных OSINT-инструментов:"
for tool in whois nmap exiftool maigret holehe subfinder; do
    if command -v "$tool" &> /dev/null; then
        echo "  🟢 $tool: $(command -v $tool)"
    else
        echo "  ⚪️ $tool: не найден (будет задействован встроенный Python-фоллбэк)"
    fi
done

echo -e "\n======================================================================"
echo "🎉 Комплекс OSINT Framework готов к работе!"
echo "Теперь вы можете в Telegram отправлять боту команды:"
echo "• /osint <домен>          (DNS, subfinder, crt.sh, RDAP, HTTP)"
echo "• /osint <IP-адрес>       (GeoIP, ASN, nmap-скан, PTR, AbuseIPDB)"
echo "• /osint <никнейм>        (Maigret скан по 3000+ сервисам)"
echo "• /osint <email>          (Holehe скан регистраций по 120+ сервисам)"
echo "• /osint <телефон>        (Международная валидация, оператор, страна)"
echo "• /osint <CVE-ID>         (NVD NIST и Exploit-DB PoC)"
echo "• /osint tools            (Интерактивный каталог OSINT Framework)"
echo "======================================================================"
