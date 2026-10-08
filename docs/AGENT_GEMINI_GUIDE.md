# Руководство по развертыванию для агента Gemini (Antigravity)

Данный документ содержит четкие пошаговые команды для развертывания системы на Ноутбуке-сервере и Основном ПК.

---

## 1. Настройка Основного ПК (RTX 2060 Super + Qwen 2.5 7B)

### Задача:
Запустить локальный инференс-сервер Qwen 2.5 7B, доступный по локальной сети на порту `11434`.

### Вариант 1 (Рекомендуемый: Ollama):
1. Установить Ollama (если не установлена) или запустить уже имеющуюся.
2. Настроить прослушивание внешних локальных подключений (по умолчанию Ollama слушает только `127.0.0.1`):
   - **Windows:** В переменных среды пользователя добавить `OLLAMA_HOST=0.0.0.0:11434`. Перезапустить Ollama.
   - **Linux:** В systemd сервисе добавить `Environment="OLLAMA_HOST=0.0.0.0:11434"`.
3. Скачать и запустить модель Qwen 2.5 7B (квантованную под 8GB VRAM):
   ```bash
   ollama run qwen2.5:7b-instruct-q4_K_M
   ```
4. Узнать локальный IP основного ПК:
   - В Windows: `ipconfig` (найти IPv4 в локальной сети, например `192.168.1.150`).
5. Проверить в брандмауэре Windows: разрешить входящие подключения на TCP порт `11434` из частной сети.

---

## 2. Настройка Ноутбука-сервера (24/7 Сервер)

### Задача:
Склонировать репозиторий, настроить конфигурацию и запустить контейнер через Docker Compose.

### Шаг 1: Подключение и клонирование
```bash
ssh user@<IP_НОУТБУКА>
cd ~
git clone https://github.com/Mikaso-Nakamoto/-.-.git ai-news-hub
cd ai-news-hub
```

### Шаг 2: Создание конфигурации
```bash
cp .env.example .env
cp config/config.example.yaml config/config.yaml
cp config/preferences.example.yaml config/preferences.yaml
cp config/sources.example.yaml config/sources.yaml
```

В `.env` внести:
```bash
nano .env
# Заполнить TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_ID, LOCAL_QWEN_URL (IP вашего ПК) и API-ключи Groq/Gemini/OpenRouter
```

### Шаг 3: Сборка и запуск в Docker
```bash
docker compose up -d --build
```

Проверка логов:
```bash
docker compose logs -f ai-agent
```

---

## 3. Настройка Tailscale (Связь вне дома и обход блокировок)

### На Ноутбуке-сервере:
1. Установить Tailscale:
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   ```
2. Запустить с включением маршрутизации и Exit-node:
   ```bash
   sudo tailscale up --advertise-exit-node --advertise-routes=192.168.1.0/24
   ```
3. В админ-панели Tailscale (в браузере):
   - Для узла ноутбука включить галочки `Approve route` и `Approve exit node`.
4. Включить IP Forwarding в Linux на ноутбуке:
   ```bash
   echo 'net.ipv4.ip_forward = 1' | sudo tee -a /etc/sysctl.d/99-tailscale.conf
   echo 'net.ipv6.conf.all.forwarding = 1' | sudo tee -a /etc/sysctl.d/99-tailscale.conf
   sudo sysctl -p /etc/sysctl.d/99-tailscale.conf
   ```

### На телефоне:
1. Установить приложение Tailscale из RuStore / Google Play / App Store.
2. Войти под тем же аккаунтом.
3. В настройках подключения выбрать ноутбук как **Exit Node**.
4. Теперь весь трафик телефона идет через домашний ноутбук, минуя ограничения мобильного оператора на белые списки.
