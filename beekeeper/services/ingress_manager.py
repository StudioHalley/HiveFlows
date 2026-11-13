"""Ingress Manager — génère la config Nginx et recharge nginx.

Stratégie :
  - Un seul fichier généré : {nginx_conf_dir}/hive-apps.conf
  - Un bloc `location` par app running (workspace_id + app_slug + container_ip + port)
  - Nginx est connecté dynamiquement aux workspace networks pour pouvoir atteindre les apps
  - Reload zero-downtime via `nginx -s reload` exécuté dans le container nginx

Canaux de données :
  container labels → hive.workspace_id, hive.app_slug, hive.app_port
  container NetworkSettings → IP dans le réseau hive-ws-{workspace_id}
"""
import logging
import os

import docker
import docker.errors

logger = logging.getLogger("beekeeper.ingress_manager")

_HIVE_APPS_CONF = "hive-apps.conf"

# Template standard : nginx stripe le préfixe avant de passer au container
_LOCATION_TEMPLATE = """\
    # {workspace_id} — {app_slug}
    location /app/{workspace_id}/{app_slug}/ {{
        proxy_pass http://{container_ip}:{port}/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_redirect ~^/(.*) /app/{workspace_id}/{app_slug}/$1;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }}
"""

# Template no-strip : le chemin complet est transmis au container (base-url configurée côté app)
_LOCATION_TEMPLATE_NO_STRIP = """\
    # {workspace_id} — {app_slug}
    location /app/{workspace_id}/{app_slug}/ {{
        proxy_pass http://{container_ip}:{port};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }}
"""

# Template rewrite : strip le préfixe + réécrit les chemins absolus dans les réponses
# Pour les apps qui ne supportent pas de base_url (ex: SilverBullet)
_LOCATION_TEMPLATE_REWRITE = """\
    # {workspace_id} — {app_slug}
    location /app/{workspace_id}/{app_slug}/ {{
        proxy_pass http://{container_ip}:{port}/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Accept-Encoding "";
        proxy_redirect ~^/(.*) /app/{workspace_id}/{app_slug}/$1;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        sub_filter_once off;
        sub_filter_types text/html text/css application/javascript application/json;
        sub_filter 'src="/' 'src="/app/{workspace_id}/{app_slug}/';
        sub_filter "src='/" "src='/app/{workspace_id}/{app_slug}/";
        sub_filter 'href="/' 'href="/app/{workspace_id}/{app_slug}/';
        sub_filter "href='/" "href='/app/{workspace_id}/{app_slug}/";
        sub_filter 'action="/' 'action="/app/{workspace_id}/{app_slug}/';
        sub_filter 'url(/' 'url(/app/{workspace_id}/{app_slug}/';
    }}
"""


# ── Données ────────────────────────────────────────────────────────────────────

def _get_running_apps(docker_client: docker.DockerClient) -> list[dict]:
    """Retourne les apps hive-managed running avec ip + port."""
    try:
        containers = docker_client.containers.list(
            filters={"label": "hive.managed=true", "status": "running"}
        )
    except docker.errors.APIError as e:
        logger.warning(f"Impossible de lister les containers: {e}")
        return []

    apps = []
    for c in containers:
        labels = c.labels or {}
        workspace_id = labels.get("hive.workspace_id")
        app_slug = labels.get("hive.app_slug")
        port_str = labels.get("hive.app_port")

        if not workspace_id or not app_slug or not port_str:
            continue

        try:
            port = int(port_str)
        except (ValueError, TypeError):
            continue

        # Trouver l'IP dans le réseau workspace
        ip = None
        net_settings = c.attrs.get("NetworkSettings", {}) if c.attrs else {}
        for net_name, net_data in net_settings.get("Networks", {}).items():
            if net_name.startswith("hive-ws-"):
                ip = net_data.get("IPAddress")
                break

        if not ip:
            logger.warning(f"Pas d'IP pour {c.name}, skipped")
            continue

        strip_prefix = labels.get("hive.strip_prefix", "true") != "false"
        rewrite_paths = labels.get("hive.rewrite_paths", "false") == "true"
        apps.append({
            "workspace_id": workspace_id,
            "app_slug": app_slug,
            "container_ip": ip,
            "port": port,
            "strip_prefix": strip_prefix,
            "rewrite_paths": rewrite_paths,
        })

    return apps


# ── Config Nginx ───────────────────────────────────────────────────────────────

def _generate_config(apps: list[dict]) -> str:
    """Génère le contenu du fichier hive-apps.conf."""
    if not apps:
        return "# Aucune app en cours d'exécution\n"

    blocks = ["# Généré automatiquement par Beekeeper — NE PAS ÉDITER\n"]
    for app in apps:
        if app.get("rewrite_paths"):
            template = _LOCATION_TEMPLATE_REWRITE
        elif app.get("strip_prefix", True):
            template = _LOCATION_TEMPLATE
        else:
            template = _LOCATION_TEMPLATE_NO_STRIP
        blocks.append(
            template.format(
                workspace_id=app["workspace_id"],
                app_slug=app["app_slug"],
                container_ip=app["container_ip"],
                port=app["port"],
            )
        )
    return "\n".join(blocks)


def _write_config(content: str, nginx_conf_dir: str) -> None:
    """Écrit hive-apps.conf dans le répertoire nginx conf.d."""
    os.makedirs(nginx_conf_dir, exist_ok=True)
    path = os.path.join(nginx_conf_dir, _HIVE_APPS_CONF)
    with open(path, "w") as f:
        f.write(content)
    logger.info(f"Config Nginx écrite: {path} ({len(content)} octets)")


# ── Réseau Nginx ───────────────────────────────────────────────────────────────

def _sync_nginx_networks(
    docker_client: docker.DockerClient,
    apps: list[dict],
    nginx_container_name: str,
) -> None:
    """Connecte nginx aux workspace networks des apps running.

    Ne déconnecte pas les réseaux existants (géré séparément pour éviter
    de couper des connexions actives).
    """
    try:
        nginx = docker_client.containers.get(nginx_container_name)
    except docker.errors.NotFound:
        logger.warning(f"Container nginx {nginx_container_name!r} introuvable — skip réseau")
        return

    # Réseaux actuels de nginx
    nginx_networks = set(
        (nginx.attrs or {}).get("NetworkSettings", {}).get("Networks", {}).keys()
    )

    # Workspace networks des apps running
    needed_networks = {f"hive-ws-{app['workspace_id']}" for app in apps}

    for net_name in needed_networks:
        if net_name in nginx_networks:
            continue
        try:
            net = docker_client.networks.get(net_name)
            net.connect(nginx)
            logger.info(f"Nginx connecté au réseau {net_name}")
        except docker.errors.NotFound:
            logger.warning(f"Réseau {net_name} introuvable — skip")
        except docker.errors.APIError as e:
            logger.warning(f"Erreur connexion nginx↔{net_name}: {e}")

    # Déconnecter nginx des workspace networks qui n'ont plus d'apps running
    for net_name in list(nginx_networks):
        if not net_name.startswith("hive-ws-"):
            continue
        if net_name not in needed_networks:
            try:
                net = docker_client.networks.get(net_name)
                net.disconnect(nginx)
                logger.info(f"Nginx déconnecté du réseau {net_name} (plus d'apps)")
            except docker.errors.APIError as e:
                logger.warning(f"Erreur déconnexion nginx↔{net_name}: {e}")


# ── Reload Nginx ───────────────────────────────────────────────────────────────

def _reload_nginx(docker_client: docker.DockerClient, nginx_container_name: str) -> None:
    """Envoie nginx -s reload dans le container nginx (zero downtime)."""
    try:
        nginx = docker_client.containers.get(nginx_container_name)
        result = nginx.exec_run("nginx -s reload", demux=False)
        exit_code = result.exit_code if hasattr(result, "exit_code") else result[0]
        if exit_code == 0:
            logger.info("Nginx rechargé avec succès")
        else:
            output = result.output if hasattr(result, "output") else result[1]
            logger.warning(f"nginx -s reload exit={exit_code}: {output}")
    except docker.errors.NotFound:
        logger.warning(f"Container nginx {nginx_container_name!r} introuvable — reload ignoré")
    except docker.errors.APIError as e:
        logger.warning(f"Erreur reload nginx: {e}")


# ── API publique ───────────────────────────────────────────────────────────────

def refresh_nginx_config(
    docker_client: docker.DockerClient,
    nginx_conf_dir: str,
    nginx_container_name: str = "nginx",
) -> None:
    """Point d'entrée principal : synchronise la config Nginx avec l'état Docker.

    À appeler après chaque start/stop/restart/remove d'une app.
    """
    apps = _get_running_apps(docker_client)
    _sync_nginx_networks(docker_client, apps, nginx_container_name)
    config = _generate_config(apps)
    _write_config(config, nginx_conf_dir)
    _reload_nginx(docker_client, nginx_container_name)
    logger.info(f"Nginx config rechargée — {len(apps)} app(s) routée(s)")
