# Интеграция с Kestra (Оркестрация процессов)

## Сравнение: Kestra vs Легковесный стек Docker

В проекте заложена возможность работы в двух режимах:

### Вариант А: Автономный легковесный контейнер (Рекомендуется для старого ноутбука)
- **Потребление RAM:** ~120–180 МБ.
- **Особенности:** Всё упаковано в один легковесный Python-процесс (Aiogram + APScheduler + SQLite). Не нагружает старый ноутбук, не греет процессор.
- **Запуск:** `docker compose up -d`

### Вариант Б: Kestra Workflow Engine
- **Потребление RAM:** ~1.5–2.5 ГБ (Java Virtual Machine + PostgreSQL).
- **Когда это имеет смысл:** Если на ноутбуке не менее 8 ГБ оперативной памяти и вы хотите иметь красивый визуальный UI со стрелочками, графиками выполнения, историей запусков и возможностью расширения до сотен пайплайнов.

---

## Пример Flow для Kestra (`daily-digest.yaml`)

Если вы решите поднять Kestra, пайплайн утреннего дайджеста на 08:50 описывается декларативно следующим YAML-файлом:

```yaml
id: morning_ai_digest
namespace: home.ai

triggers:
  - id: daily_0850_schedule
    type: io.kestra.plugin.core.trigger.Schedule
    cron: "50 8 * * *"
    timezone: "Europe/Moscow"

tasks:
  - id: fetch_telegram_and_rss
    type: io.kestra.plugin.scripts.python.Commands
    docker:
      image: python:3.12-slim
    beforeCommands:
      - pip install httpx beautifulsoup4 feedparser
    commands:
      - python -c "print('Сбор свежих новостей из каналов...')"

  - id: route_to_llm
    type: io.kestra.plugin.core.http.Request
    uri: "http://192.168.1.150:11434/v1/chat/completions" # Qwen 2.5 на ПК
    method: POST
    headers:
      Content-Type: "application/json"
    body: |
      {
        "model": "qwen2.5:7b-instruct-q4_K_M",
        "messages": [
          {"role": "user", "content": "Сделай выжимку новостей..."}
        ]
      }
    # Авто-фоллбэк на Groq/Gemini при недоступности локального ПК
    retry:
      maxAttempt: 2
      type: constant
      interval: PT2S

  - id: send_telegram_message
    type: io.kestra.plugin.notifications.telegram.TelegramExecution
    token: "{{ secret('TELEGRAM_BOT_TOKEN') }}"
    channel: "{{ secret('TELEGRAM_CHAT_ID') }}"
    message: "☀️ Утренний дайджест готов!"
```
