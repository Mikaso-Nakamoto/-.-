#!/usr/bin/env bash
# ==============================================================================
# AI News Hub — Автоматический установщик для Ноутбука-сервера (2026)
# Подходит для запуска агентом Gemini в Antigravity или вручную через SSH
# ==============================================================================

set -e

# Цвета для красивого вывода
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}======================================================${NC}"
echo -e "${GREEN}   🤖 Запуск автоустановщика AI News Hub (2026)       ${NC}"
echo -e "${BLUE}======================================================${NC}"

# 1. Проверка прав (не запускать от чистого root без надобности)
CURRENT_USER=$(whoami)
echo -e "${BLUE}[1/6] Текущий пользователь:${NC} ${CURRENT_USER}"

# 2. Проверка и установка Docker & Docker Compose
echo -e "${BLUE}[2/6] Проверка Docker и Docker Compose...${NC}"
if ! command -v docker &> /dev/null; then
    echo -e "${YELLOW}Docker не найден. Устанавливаем Docker...${NC}"
    sudo apt-get update
    sudo apt-get install -y ca-certificates curl gnupg lsb-release
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg --yes
    sudo chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    sudo usermod -aG docker "$CURRENT_USER"
    echo -e "${GREEN}Docker успешно установлен!${NC}"
else
    echo -e "${GREEN}Docker уже установлен.${NC}"
fi

# 3. Клонирование репозитория
APP_DIR="$HOME/ai-news-hub"
echo -e "${BLUE}[3/6] Проверка директории проекта (${APP_DIR})...${NC}"

if [ -d "$APP_DIR/.git" ]; then
    echo -e "${YELLOW}Проект уже существует. Обновляем кодовую базу...${NC}"
    cd "$APP_DIR"
    git fetch origin
    git checkout arena/4eea9db7-repo
    git pull origin arena/4eea9db7-repo
else
    echo -e "${YELLOW}Клонируем репозиторий из GitHub...${NC}"
    git clone -b arena/4eea9db7-repo https://github.com/Mikaso-Nakamoto/-.-.git "$APP_DIR"
    cd "$APP_DIR"
fi

# 4. Инициализация конфигурационных файлов
echo -e "${BLUE}[4/6] Инициализация конфигурационных файлов...${NC}"

if [ ! -f .env ]; then
    cp .env.example .env
    echo -e "${YELLOW}Создан файл .env из шаблона .env.example${NC}"
fi

if [ ! -f config/config.yaml ]; then
    cp config/config.example.yaml config/config.yaml
    echo -e "${YELLOW}Создан config/config.yaml${NC}"
fi

if [ ! -f config/preferences.yaml ]; then
    cp config/preferences.example.yaml config/preferences.yaml
    echo -e "${YELLOW}Создан config/preferences.yaml${NC}"
fi

if [ ! -f config/sources.yaml ]; then
    cp config/sources.example.yaml config/sources.yaml
    echo -e "${YELLOW}Создан config/sources.yaml${NC}"
fi

if [ ! -f config/prompts.yaml ]; then
    cp config/prompts.example.yaml config/prompts.yaml
    echo -e "${YELLOW}Создан config/prompts.yaml${NC}"
fi

mkdir -p data

# 5. Проверка заполненности .env
echo -e "${BLUE}[5/6] Проверка переменных окружения (.env)...${NC}"
if grep -q "123456789:AAFg" .env || grep -q "YOUR_TELEGRAM_BOT_TOKEN" config/config.yaml; then
    echo -e "${RED}ВНИМАНИЕ! В файле .env указаны шаблонные ключи!${NC}"
    echo -e "${YELLOW}Пожалуйста, откройте .env командой: nano $APP_DIR/.env${NC}"
    echo -e "${YELLOW}И внесите туда ваш реальный TELEGRAM_BOT_TOKEN и TELEGRAM_ADMIN_ID.${NC}"
fi

# 6. Сборка и запуск контейнеров
echo -e "${BLUE}[6/6] Сборка и запуск контейнеров в Docker...${NC}"
if docker compose version &> /dev/null; then
    docker compose up -d --build
else
    sudo docker compose up -d --build
fi

echo -e "${BLUE}======================================================${NC}"
echo -e "${GREEN}   ✅ AI News Hub успешно установлен и запущен!       ${NC}"
echo -e "${BLUE}======================================================${NC}"
echo -e "Полезные команды:"
echo -e "  • Посмотреть логи бота:    ${YELLOW}cd ~/ai-news-hub && docker compose logs -f ai-agent${NC}"
echo -e "  • Перезапустить систему:   ${YELLOW}cd ~/ai-news-hub && docker compose restart${NC}"
echo -e "  • Редактировать токены:    ${YELLOW}nano ~/ai-news-hub/.env${NC}"
echo -e "  • Редактировать промпты:   ${YELLOW}nano ~/ai-news-hub/config/prompts.yaml${NC}"
