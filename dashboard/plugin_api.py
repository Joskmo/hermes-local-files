"""Backend API безопасного создания Local Files проектов.

Маршруты монтируются Hermes в ``/api/plugins/local-files/`` и защищены
штатной gateway/dashboard-аутентификацией.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from hermes_local_files.server import ServerProvisioner
from hermes_local_files.syncthing import SyncthingClient, SyncthingError


router = APIRouter()
_MAPPING_RE = re.compile(r"^[a-f0-9]{32}$")
_DEVICE_RE = re.compile(r"^[A-Z0-9-]+$")
_DEFAULT_CONFIG = Path.home() / ".config/hermes-local-files/server.json"


class ProvisionRequest(BaseModel):
    """Минимальный запрос без клиентских server path или credentials."""

    mapping_id: str
    name: str
    local_device_id: str


def _private_config() -> Dict[str, Any]:
    path = Path(os.environ.get("HERMES_LOCAL_FILES_SERVER_CONFIG", _DEFAULT_CONFIG))
    try:
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            raise RuntimeError("Server config must be owner-only (mode 0600)")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except RuntimeError:
        raise
    except (OSError, ValueError) as exc:
        raise RuntimeError("Local Files server config is unavailable") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Local Files server config must be an object")
    root = Path(str(payload.get("projects_root") or "")).expanduser().resolve()
    allowed = Path("/srv").resolve()
    if not root.is_absolute() or root == allowed or allowed not in root.parents:
        raise RuntimeError("Local Files projects root must be below /srv")
    if not str(payload.get("syncthing_api_key") or ""):
        raise RuntimeError("Syncthing API key is missing")
    return {
        "projects_root": root,
        "syncthing_api_url": str(
            payload.get("syncthing_api_url") or "http://127.0.0.1:8384"
        ),
        "syncthing_api_key": str(payload["syncthing_api_key"]),
    }


def _provisioner() -> ServerProvisioner:
    config = _private_config()
    syncthing = SyncthingClient(
        config["syncthing_api_url"],
        config["syncthing_api_key"],
    )
    return ServerProvisioner(config["projects_root"], syncthing)


@router.get("/v1/health")
def health() -> Dict[str, Any]:
    """Проверить backend без раскрытия credentials."""

    try:
        provisioner = _provisioner()
        device_id = provisioner.syncthing.device_id()
    except (RuntimeError, SyncthingError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "ok": True,
        "server_device_id": device_id,
        "projects_root": str(provisioner.root),
    }


@router.post("/v1/provision")
def provision(body: ProvisionRequest) -> Dict[str, str]:
    """Идемпотентно создать серверную сторону одной локальной папки."""

    mapping_id = body.mapping_id.strip()
    name = body.name.strip()
    local_device_id = body.local_device_id.strip()
    if not _MAPPING_RE.fullmatch(mapping_id):
        raise HTTPException(status_code=400, detail="Invalid mapping id")
    if not name or len(name) > 120:
        raise HTTPException(status_code=400, detail="Project name must be 1–120 characters")
    if not _DEVICE_RE.fullmatch(local_device_id):
        raise HTTPException(status_code=400, detail="Invalid Syncthing device id")
    try:
        return _provisioner().provision(mapping_id, name, local_device_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (RuntimeError, SyncthingError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
