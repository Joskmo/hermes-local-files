"""Серверное создание синхронизируемых проектов."""

import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

from .core import build_folder_config, safe_project_path, stable_folder_id


DEFAULT_IGNORES: List[str] = [
    "(?d).DS_Store",
    "(?d).env",
    "(?d).env.*",
    "(?d).hermes-local-files.json",
    "(?d).Spotlight-V100",
    "(?d).Trashes",
    "(?d)node_modules",
    "(?d).git",
    "(?d)__pycache__",
    "(?d).pytest_cache",
    "(?d).mypy_cache",
    "(?d).ruff_cache",
]
_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9-]+$")
_MANIFEST = ".hermes-local-files.json"


class ServerProvisioner:
    """Создаёт project roots только внутри одного доверенного каталога."""

    def __init__(self, root: Path, syncthing: Any):
        self.root = root.resolve()
        self.syncthing = syncthing

    def _existing_mapping(self, mapping_id: str) -> Optional[Dict[str, Any]]:
        if not self.root.is_dir():
            return None
        for manifest in self.root.glob(f"*/{_MANIFEST}"):
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if payload.get("mapping_id") == mapping_id:
                payload["server_path"] = str(manifest.parent.resolve())
                return payload
        return None

    @staticmethod
    def _write_manifest(path: Path, payload: Dict[str, Any]) -> None:
        target = path / _MANIFEST
        temporary = path / f"{_MANIFEST}.tmp"
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        os.replace(temporary, target)

    def provision(
        self,
        mapping_id: str,
        name: str,
        local_device_id: str,
    ) -> Dict[str, str]:
        """Идемпотентно создать папку и зарегистрировать её в Syncthing."""

        if not mapping_id.strip():
            raise ValueError("Mapping id is required")
        if not _DEVICE_ID_RE.fullmatch(local_device_id):
            raise ValueError("Invalid local Syncthing device id")
        destination = safe_project_path(self.root, name)
        existing = self._existing_mapping(mapping_id)
        if existing and Path(existing["server_path"]) != destination:
            raise ValueError("Mapping is already bound to another server path")
        if destination.exists():
            manifest = destination / _MANIFEST
            try:
                owner = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                owner = {}
            if owner.get("mapping_id") != mapping_id:
                raise ValueError("Project path is already used by another mapping")

        folder_id = stable_folder_id(mapping_id)
        server_device_id = self.syncthing.device_id()
        result = {
            "mapping_id": mapping_id,
            "name": name.strip(),
            "folder_id": folder_id,
            "server_path": str(destination),
            "server_device_id": server_device_id,
        }

        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination.mkdir(mode=0o700, exist_ok=True)
        self._write_manifest(destination, result)
        self.syncthing.put_device(
            device_id=local_device_id,
            name="Client Mac",
            addresses=["dynamic"],
        )
        folder = build_folder_config(
            folder_id=folder_id,
            label=name.strip(),
            path=str(destination),
            remote_device_id=local_device_id,
            server=True,
        )
        self.syncthing.put_folder(folder)
        self.syncthing.set_ignores(folder_id, DEFAULT_IGNORES)
        self.syncthing.rescan(folder_id)
        return result
