"""Manifest Loader — lit les manifests YAML depuis /apps/ et synchronise app_catalog en DB.

Chaque sous-répertoire de apps_dir doit contenir un fichier manifest.yaml.
La sync est idempotente : INSERT ... ON CONFLICT (slug) DO UPDATE.

Format manifest.yaml :
  name, slug, version, description, image, type, ingress, port,
  env (list), mounts (list), resources (dict), icon
"""
import json
import logging
import os
from pathlib import Path

import sqlalchemy as sa
import yaml

logger = logging.getLogger("beekeeper.manifest_loader")


def _parse_manifest(path: Path) -> dict | None:
    """Lit et valide un manifest YAML. Retourne None si invalide."""
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except Exception as e:
        logger.warning(f"Impossible de lire {path}: {e}")
        return None

    required = ("name", "slug", "image")
    for field in required:
        if not data.get(field):
            logger.warning(f"{path}: champ requis manquant: {field!r}")
            return None

    return {
        "name": str(data["name"]),
        "slug": str(data["slug"]),
        "version": str(data.get("version", "1.0.0")),
        "description": data.get("description") or None,
        "image": str(data["image"]),
        "app_type": str(data.get("type", "persistent")),
        "ingress_mode": str(data.get("ingress", "iframe")),
        "default_port": int(data["port"]) if data.get("port") else None,
        "env_schema": data.get("env") or [],
        "default_mounts": data.get("mounts") or [],
        "resources": data.get("resources") or {},
        "icon": data.get("icon") or None,
        "tools_schema": data.get("tools") or [],
    }


def _upsert_app(engine: sa.Engine, manifest: dict) -> str:
    """Upsert une app dans app_catalog. Retourne 'inserted' ou 'updated'."""
    upsert_sql = sa.text("""
        INSERT INTO app_catalog
            (name, slug, version, description, image, app_type, ingress_mode,
             default_port, env_schema, default_mounts, resources, icon, tools_schema, is_active)
        VALUES
            (:name, :slug, :version, :description, :image, :app_type, :ingress_mode,
             :default_port, CAST(:env_schema AS JSONB), CAST(:default_mounts AS JSONB),
             CAST(:resources AS JSONB), :icon, CAST(:tools_schema AS JSONB), true)
        ON CONFLICT (slug) DO UPDATE SET
            name         = EXCLUDED.name,
            version      = EXCLUDED.version,
            description  = EXCLUDED.description,
            image        = EXCLUDED.image,
            app_type     = EXCLUDED.app_type,
            ingress_mode = EXCLUDED.ingress_mode,
            default_port = EXCLUDED.default_port,
            env_schema   = EXCLUDED.env_schema,
            default_mounts = EXCLUDED.default_mounts,
            resources    = EXCLUDED.resources,
            icon         = EXCLUDED.icon,
            tools_schema = EXCLUDED.tools_schema,
            updated_at   = NOW()
        RETURNING (xmax = 0) AS inserted
    """)

    params = {
        **manifest,
        "env_schema": json.dumps(manifest["env_schema"]),
        "default_mounts": json.dumps(manifest["default_mounts"]),
        "resources": json.dumps(manifest["resources"]),
        "tools_schema": json.dumps(manifest["tools_schema"]),
    }

    with engine.begin() as conn:
        row = conn.execute(upsert_sql, params).fetchone()
        return "inserted" if row and row[0] else "updated"


def sync_manifests(engine: sa.Engine, apps_dir: str) -> dict:
    """Scanne apps_dir, parse chaque manifest.yaml, upsert dans app_catalog.

    Retourne un résumé : {"inserted": N, "updated": N, "errors": N}
    """
    apps_path = Path(apps_dir)
    if not apps_path.is_dir():
        logger.warning(f"Répertoire apps introuvable: {apps_dir}")
        return {"inserted": 0, "updated": 0, "errors": 0}

    inserted, updated, errors = 0, 0, 0

    for entry in sorted(apps_path.iterdir()):
        if not entry.is_dir():
            continue
        manifest_file = entry / "manifest.yaml"
        if not manifest_file.exists():
            logger.debug(f"Pas de manifest.yaml dans {entry.name}, skipped")
            continue

        manifest = _parse_manifest(manifest_file)
        if manifest is None:
            errors += 1
            continue

        try:
            action = _upsert_app(engine, manifest)
            if action == "inserted":
                inserted += 1
                logger.info(f"App ajoutée au catalogue: {manifest['slug']} ({manifest['name']})")
            else:
                updated += 1
                logger.info(f"App mise à jour: {manifest['slug']} v{manifest['version']}")
        except Exception as e:
            errors += 1
            logger.error(f"Erreur upsert {manifest.get('slug')}: {e}", exc_info=True)

    logger.info(
        f"Sync manifests terminée — {inserted} ajoutée(s), {updated} mise(s) à jour, {errors} erreur(s)"
    )
    return {"inserted": inserted, "updated": updated, "errors": errors}
