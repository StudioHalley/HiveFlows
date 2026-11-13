FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Pas de --reload en prod : le code vient de l'image (immuable).
# Le hot-reload + bind-mount est réactivé en dev via docker-compose.override.yml.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
