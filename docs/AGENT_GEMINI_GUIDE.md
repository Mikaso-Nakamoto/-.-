# Руководство по развертыванию для агента Gemini (Antigravity)

Данный документ содержит четкие пошаговые команды для развертывания системы на Ноутбуке-сервере и Основном ПК.

---

## 1. Настройка Основного ПК (RTX 2060 Super + Qwen 2.5 7B в LM Studio)

### Задача:
Запустить локальный инференс-сервер Qwen 2.5 7B через **LM Studio**, доступный по локальной сети на порту `1234`.

### Пошаговые действия в LM Studio:
1. **Запустить LM Studio** на основном ПК.
2. **Загрузить модель Qwen 2.5 7B Instruct** (формат GGUF, рекомендуемый квант `Q4_K_M` или `Q5_K_M`).
3. **Настроить GPU Offload (для RTX 2060 Super 8GB):**
   - В правой панели настроек выставить ползунок **GPU Offload** на максимум (все слои в GPU).
   - Это гарантирует, что инференс будет происходить быстро на тензорных ядрах видеокарты, а не на процессоре.
4. **Запустить локальный сервер:**
   - Перейти на вкладку **Developer / Local Server** (иконка со стрелочками `<->` или `Server` на левой панели).
   - Сверху выбрать модель `Qwen 2.5 7B`.
   - **ОЧЕНЬ ВАЖНО:** Включить переключатель **"Serve on Local Network" / "Network Serving"** (привязать к `0.0.0.0` вместо `127.0.0.1`), чтобы ноутбук по кабелю мог достучаться до ПК.
   - Нажать кнопку **"Start Server"**. Порт по умолчанию — `1234`.
5. **Узнать IP основного ПК:**
   - В терминале (cmd/powershell): `ipconfig` -> IPv4-адрес частной сети (например, `192.168.1.150`).
   - Итоговый адрес для ноутбука: `http://192.168.1.150:1234/v1`.
6. **Брандмауэр Windows:**
   - Если при первом запуске Windows спросит разрешение брандмауэра для LM Studio — разрешить для "Частных сетей".

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
