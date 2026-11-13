import os
from pathlib import Path
from typing import Any
import yaml

WORKSPACE = Path(os.environ.get("WORKSPACE_PATH", "/workspace"))
CONFIG_PATH = WORKSPACE / ".hive" / "remote-fs" / "config.yaml"
DATA_DIR = WORKSPACE / ".hive" / "remote-fs"
DB_PATH = DATA_DIR / "remote-fs.db"

# Sub-directory that gets synced (default: "shared/").
# Set sync.root: "." in config.yaml to sync the entire workspace.
_sync_root_rel = os.environ.get("SYNC_ROOT", "")  # env override for tests

_DEFAULTS: dict[str, Any] = {
    "node": {
        "id": os.environ.get("NODE_ID", "hiveflows-node"),
        "workspace": str(WORKSPACE),
    },
    "sync": {
        "root": "shared",
        "chunk_size": 1048576,
        "conflict_strategy": "last_write_wins",
        "ignore": [".hive/", "*.tmp", "__pycache__/"],
    },
    "peers": [],
    "portal": {
        "enabled": False,
        "api_url": "http://core:8000",
        "device_group": "hiveflows",
    },
    "webdav": {
        "enabled": False,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        return _DEFAULTS
    with open(CONFIG_PATH) as f:
        user_cfg = yaml.safe_load(f) or {}
    return _deep_merge(_DEFAULTS, user_cfg)


cfg = load()

# SYNC_ROOT: the directory actually watched and transferred.
# Priority: SYNC_ROOT env var > config.yaml sync.root > default "shared"
_root_rel = _sync_root_rel or cfg["sync"].get("root", "shared")
SYNC_ROOT = (WORKSPACE / _root_rel).resolve() if _root_rel != "." else WORKSPACE.resolve()
SYNC_ROOT.mkdir(parents=True, exist_ok=True)
