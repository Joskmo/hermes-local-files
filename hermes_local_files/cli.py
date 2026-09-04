"""Запуск macOS companion процесса."""

import argparse
import json
from pathlib import Path
import socket
import subprocess
from typing import Any, Dict

from .companion import LocalProvisioner, MappingStore
from .companion_api import CompanionApp, make_server
from .syncthing import SyncthingClient


_DEFAULT_CONFIG = (
    Path.home() / "Library/Application Support/Hermes Local Files/config.json"
)


def load_private_config(path: Path) -> Dict[str, Any]:
    """Прочитать owner-only конфигурацию без неявных значений secrets."""

    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise ValueError("Companion config must be owner-only (mode 0600)")
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = ("token", "syncthing_api_key", "syncthing_api_url", "tunnel_port")
    if not isinstance(payload, dict) or any(not payload.get(key) for key in required):
        raise ValueError("Companion config is incomplete")
    return payload


def pick_macos_folder() -> str:
    """Показать нативный выбор папки Finder и вернуть абсолютный POSIX path."""

    script = (
        'set selectedFolder to choose folder with prompt '
        '"Выберите папку для работы с Hermes"\n'
        "POSIX path of selectedFolder"
    )
    result = subprocess.run(
        ["/usr/bin/osascript", "-e", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode:
        raise ValueError("Folder selection was cancelled")
    path = result.stdout.strip().rstrip("/")
    if not path or not Path(path).is_dir():
        raise ValueError("Selected local folder does not exist")
    return path


def port_open(port: int) -> bool:
    """Проверить локальный tunnel listener без внешнего сетевого запроса."""

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def serve(config_path: Path) -> None:
    """Запустить loopback API до остановки LaunchAgent."""

    config = load_private_config(config_path)
    store_path = Path(
        config.get("mapping_path")
        or config_path.parent / "mappings.json"
    ).expanduser()
    syncthing = SyncthingClient(
        str(config["syncthing_api_url"]),
        str(config["syncthing_api_key"]),
    )
    provisioner = LocalProvisioner(
        syncthing,
        MappingStore(store_path),
        tunnel_port=int(config["tunnel_port"]),
    )
    app = CompanionApp(
        token=str(config["token"]),
        picker=pick_macos_folder,
        device_id=syncthing.device_id,
        provisioner=provisioner,
        store=MappingStore(store_path),
    )
    server = make_server(
        "127.0.0.1",
        int(config.get("companion_port") or 45671),
        app,
    )
    server.serve_forever(poll_interval=0.5)


def main() -> None:
    parser = argparse.ArgumentParser(description="Hermes Local Files companion")
    parser.add_argument(
        "--config",
        type=Path,
        default=_DEFAULT_CONFIG,
        help="owner-only companion JSON config",
    )
    args = parser.parse_args()
    serve(args.config.expanduser())


if __name__ == "__main__":
    main()
