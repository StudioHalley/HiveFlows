from pydantic import computed_field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Auth — clé interne pour les appels Core → Beekeeper
    beekeeper_api_key: str = "change-me-beekeeper-key"

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # Docker socket
    docker_socket: str = "unix:///var/run/docker.sock"

    # Credential Vault — même clé Fernet que core pour déchiffrer les env vars
    credential_encryption_key: str = ""

    # PostgreSQL — vars individuelles (compatibles avec le .env existant)
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "hiveflows"
    postgres_user: str = "hiveflows"
    postgres_password: str = "changeme"

    @computed_field
    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # Répertoire des manifests apps (monté en volume depuis ./apps)
    apps_dir: str = "/apps"

    # Répertoire de base pour les workspaces — chemin INTERNE au container beekeeper
    # (utilisé pour os.makedirs — le volume est monté ici)
    workspaces_data_dir: str = "/workspace"

    # Chemin HOST équivalent — transmis à Docker pour les specs de volumes des app containers.
    # Doit être un chemin ABSOLU sur l'hôte Docker (ex: /home/patrick/hiveflows/workspace_data).
    # Si vide, fallback sur workspaces_data_dir (fonctionne uniquement si beekeeper tourne
    # directement sur l'hôte, pas en container).
    workspaces_data_host_dir: str = ""

    # Réseau Docker partagé — les apps de type "backend" y sont connectées
    # pour être accessibles depuis le container core (Docker DNS).
    hive_backend_network: str = "hive-backend"

    # Nom du container Redis sur l'hôte Docker (pour le connecter à hive-backend).
    # docker-compose nomme les containers {project}-{service}-{n}, ex: hiveflows-redis-1
    redis_container_name: str = "hiveflows-redis-1"

    # Nginx — répertoire des configs générées + nom du container
    nginx_conf_dir: str = "/nginx/conf.d"
    nginx_container_name: str = "nginx"

    # Self-update — chemin HOST du repo, monté à l'identique dans beekeeper.
    # Vide = self-update UI désactivé (mise à jour possible seulement en CLI/SSH).
    hive_repo_host_dir: str = ""

    log_level: str = "INFO"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
