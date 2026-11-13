FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    git \
    curl \
    gzip \
    util-linux \
    && rm -rf /var/lib/apt/lists/*

# CLI Docker + plugin compose (binaires statiques, x86-64 uniquement — cf. §6 Update.md).
# Permet à beekeeper de piloter `docker compose` sur l'hôte via le socket monté,
# pour exécuter scripts/update.sh (self-update déclenché depuis l'UI).
ARG DOCKER_CLI_VERSION=27.5.1
ARG COMPOSE_VERSION=v2.32.4
RUN curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_CLI_VERSION}.tgz" \
      | tar xz -C /usr/local/bin --strip-components=1 docker/docker \
 && mkdir -p /usr/local/lib/docker/cli-plugins \
 && curl -fsSL "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-x86_64" \
      -o /usr/local/lib/docker/cli-plugins/docker-compose \
 && chmod +x /usr/local/lib/docker/cli-plugins/docker-compose \
 && docker --version && docker compose version

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Pas de --reload en prod : le code vient de l'image (immuable).
# Le hot-reload + bind-mount est réactivé en dev via docker-compose.override.yml.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8010"]
