FROM python:3.12-slim

WORKDIR /app

# Системные зависимости для сборки C-модулей если понадобятся
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Создание директории для БД и данных
RUN mkdir -p /app/data /app/config

VOLUME ["/app/data", "/app/config"]

CMD ["python", "-m", "src.main"]
