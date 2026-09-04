"""Локальное состояние и настройка macOS companion."""

import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

from .core import build_folder_config, conflict_paths, reduce_two_sided_status
from .server import DEFAULT_IGNORES


_SSH_TARGET_RE = re.compile(r"^[A-Za-z0-9_.@-]+$")


class MappingStore:
    """Атомарное owner-only хранилище связей локальных папок."""

    def __init__(self, path: Path):
        self.path = path

    def list(self) -> List[Dict[str, Any]]:
        """Прочитать связи в стабильном порядке."""

        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("Could not read local project mappings") from exc
        if not isinstance(payload, list):
            raise ValueError("Local project mappings must be a list")
        return [item for item in payload if isinstance(item, dict)]

    def put(self, mapping: Dict[str, Any]) -> None:
        """Добавить или обновить связь без дублирования локального root."""

        mapping_id = str(mapping.get("mapping_id") or "").strip()
        local_path = str(mapping.get("local_path") or "").strip()
        if not mapping_id or not local_path:
            raise ValueError("Mapping id and local path are required")
        normalized = str(Path(local_path).expanduser().resolve())
        current = self.list()
        for item in current:
            if item.get("mapping_id") != mapping_id:
                other = str(Path(str(item.get("local_path") or "")).expanduser().resolve())
                if other == normalized:
                    raise ValueError("This local folder is already synchronized")
        clean = dict(mapping)
        clean["mapping_id"] = mapping_id
        clean["local_path"] = normalized
        updated = [item for item in current if item.get("mapping_id") != mapping_id]
        updated.append(clean)
        updated.sort(key=lambda item: str(item.get("name") or "").casefold())
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(updated, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        os.replace(temporary, self.path)


class TunnelCommand:
    """Безопасно формирует argv постоянного SSH local-forward."""

    def __init__(
        self,
        ssh_target: str,
        local_port: int,
        remote_port: int,
        ssh_port: int = 22,
        identity_file: Optional[Path] = None,
        known_hosts_file: Optional[Path] = None,
    ):
        self.ssh_target = ssh_target
        self.local_port = local_port
        self.remote_port = remote_port
        self.ssh_port = ssh_port
        self.identity_file = identity_file
        self.known_hosts_file = known_hosts_file

    def argv(self) -> List[str]:
        """Вернуть argv без shell и без публичных bind-адресов."""

        if not _SSH_TARGET_RE.fullmatch(self.ssh_target):
            raise ValueError("Unsafe SSH target")
        for port in (self.local_port, self.remote_port, self.ssh_port):
            if port < 1024 or port > 65535:
                raise ValueError("Tunnel ports must be unprivileged TCP ports")
        forward = f"127.0.0.1:{self.local_port}:127.0.0.1:{self.remote_port}"
        command = [
            "/usr/bin/ssh",
            "-NT",
            "-o", "BatchMode=yes",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=20",
            "-o", "ServerAliveCountMax=3",
            "-o", "StrictHostKeyChecking=yes",
            "-p", str(self.ssh_port),
            "-L", forward,
        ]
        if self.known_hosts_file is not None:
            known_hosts = self.known_hosts_file.expanduser().resolve()
            if not known_hosts.is_file():
                raise ValueError("SSH known_hosts file does not exist")
            command.extend([
                "-o", f"UserKnownHostsFile={known_hosts}",
                "-o", "GlobalKnownHostsFile=/dev/null",
            ])
        if self.identity_file is not None:
            identity = self.identity_file.expanduser().resolve()
            if not identity.is_file():
                raise ValueError("SSH identity file does not exist")
            command.extend(["-o", "IdentitiesOnly=yes", "-i", str(identity)])
        command.append(self.ssh_target)
        return command


class StatusStore:
    """Строит безопасный status snapshot для всех локальных связей."""

    def __init__(self, mappings: MappingStore, syncthing: Any, tunnel_up: Any):
        self.mappings = mappings
        self.syncthing = syncthing
        self.tunnel_up = tunnel_up

    def list(self) -> List[Dict[str, Any]]:
        """Вернуть связи с технически подтверждённым простым статусом."""

        result = []
        for mapping in self.mappings.list():
            local = Path(str(mapping.get("local_path") or ""))
            conflicts = [str(path.relative_to(local)) for path in conflict_paths(local)]
            tunnel = bool(self.tunnel_up())
            try:
                metrics = self.syncthing.folder_status(
                    str(mapping["folder_id"]),
                    str(mapping["server_device_id"]),
                )
                completion = float(metrics.get("remote_completion") or 0.0)
                status = reduce_two_sided_status(
                    metrics,
                    metrics,
                    conflicts=len(conflicts),
                ) if tunnel else "offline"
            except Exception:
                completion = 0.0
                status = "offline" if not tunnel else "attention"
            result.append({
                **mapping,
                "status": status,
                "completion": completion,
                "conflicts": conflicts,
            })
        return result


class LocalProvisioner:
    """Связывает выбранную папку Mac с подготовленной серверной папкой."""

    def __init__(self, syncthing: Any, store: MappingStore, tunnel_port: int):
        self.syncthing = syncthing
        self.store = store
        self.tunnel_port = tunnel_port

    def provision(self, local_path: str, server: Dict[str, Any]) -> Dict[str, Any]:
        """Идемпотентно настроить локальную сторону Syncthing."""

        local = Path(local_path).expanduser().resolve()
        if not local.is_dir():
            raise ValueError("Selected local folder does not exist")
        required = (
            "mapping_id", "name", "folder_id", "server_path", "server_device_id"
        )
        if any(not str(server.get(key) or "").strip() for key in required):
            raise ValueError("Server provisioning response is incomplete")

        server_device_id = str(server["server_device_id"])
        local_device_id = self.syncthing.device_id()
        self.syncthing.put_device(
            device_id=server_device_id,
            name="Home server",
            addresses=[f"tcp://127.0.0.1:{self.tunnel_port}"],
        )
        folder = build_folder_config(
            folder_id=str(server["folder_id"]),
            label=str(server["name"]),
            path=str(local),
            remote_device_id=server_device_id,
            server=False,
        )
        self.syncthing.put_folder(folder)
        self.syncthing.set_ignores(str(server["folder_id"]), DEFAULT_IGNORES)
        self.syncthing.rescan(str(server["folder_id"]))

        mapping = dict(server)
        mapping["local_path"] = str(local)
        mapping["local_device_id"] = local_device_id
        self.store.put(mapping)
        return mapping

    def status(self, folder_id: str) -> Dict[str, Any]:
        """Свести локальный authoritative статус к модели простого UI."""

        mapping = next(
            (item for item in self.store.list() if item.get("folder_id") == folder_id),
            None,
        )
        if mapping is None:
            raise ValueError("Unknown synchronized folder")
        local_path = Path(str(mapping["local_path"]))
        conflicts = [str(path.relative_to(local_path)) for path in conflict_paths(local_path)]
        metrics = self.syncthing.folder_status(
            folder_id,
            str(mapping["server_device_id"]),
        )
        completion = float(metrics.get("remote_completion") or 0.0)
        state = reduce_two_sided_status(
            metrics,
            metrics,
            conflicts=len(conflicts),
        )
        return {
            "folder_id": folder_id,
            "state": state,
            "conflicts": conflicts,
            "completion": completion,
            "connected": bool(metrics.get("connected")),
        }
