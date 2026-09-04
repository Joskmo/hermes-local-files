#!/usr/bin/env python3
"""Установить приватный Syncthing backend для Hermes Local Files."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
from urllib.request import urlopen
from xml.etree import ElementTree


VERSION = "2.1.3"
ARCHIVE = f"syncthing-linux-amd64-v{VERSION}.tar.gz"
DOWNLOAD_URL = f"https://github.com/syncthing/syncthing/releases/download/v{VERSION}/{ARCHIVE}"
EXPECTED_SHA256 = "f929eb8e5b72a85543eeeefb2c38f34a68e0c530e70758a2905b78840c76602c"


def run(*argv: str) -> None:
    subprocess.run(list(argv), check=True)


def download_verified(destination: Path) -> None:
    """Скачать exact immutable asset и проверить GitHub digest."""

    digest = sha256()
    with urlopen(DOWNLOAD_URL, timeout=60) as response, destination.open("wb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            output.write(chunk)
    if digest.hexdigest() != EXPECTED_SHA256:
        destination.unlink(missing_ok=True)
        raise RuntimeError("Syncthing archive checksum mismatch")


def safe_extract(archive: Path, destination: Path) -> Path:
    """Извлечь только обычные файлы строго внутри временного каталога."""

    root = destination.resolve()
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle.getmembers():
            target = (root / member.name).resolve()
            if root != target and root not in target.parents:
                raise RuntimeError("Syncthing archive contains an unsafe path")
            if member.issym() or member.islnk() or member.isdev():
                raise RuntimeError("Syncthing archive contains an unsupported entry")
        bundle.extractall(root)
    candidates = list(root.glob("syncthing-*/syncthing"))
    if len(candidates) != 1 or not candidates[0].is_file():
        raise RuntimeError("Syncthing executable was not found in the archive")
    return candidates[0]


def install_binary(install_dir: Path) -> Path:
    """Всегда заменить executable из заново проверенного immutable asset."""

    install_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    binary = install_dir / f"syncthing-{VERSION}"
    with tempfile.TemporaryDirectory(prefix="hermes-local-files-") as raw:
        temporary = Path(raw)
        archive = temporary / ARCHIVE
        download_verified(archive)
        extracted = safe_extract(archive, temporary / "extract")
        staged = install_dir / f".{binary.name}.new"
        shutil.copy2(extracted, staged)
        staged.chmod(0o700)
        os.replace(staged, binary)
    return binary


def set_text(root: ElementTree.Element, path: str, value: str) -> None:
    node = root.find(path)
    if node is None:
        raise RuntimeError(f"Syncthing config is missing {path}")
    node.text = value


def configure_xml(path: Path) -> str:
    """Запретить discovery/relay/NAT и вернуть API key без печати."""

    tree = ElementTree.parse(path)
    root = tree.getroot()
    set_text(root, "gui/address", "127.0.0.1:8384")
    set_text(root, "options/listenAddress", "tcp://127.0.0.1:22000")
    for field in (
        "globalAnnounceEnabled",
        "localAnnounceEnabled",
        "relaysEnabled",
        "natEnabled",
        "startBrowser",
    ):
        set_text(root, f"options/{field}", "false")
    api_key = (root.findtext("gui/apikey") or "").strip()
    if not api_key:
        raise RuntimeError("Generated Syncthing config has no API key")
    temporary = path.with_suffix(".xml.tmp")
    tree.write(temporary, encoding="utf-8", xml_declaration=True)
    temporary.chmod(0o600)
    os.replace(temporary, path)
    return api_key


def write_json_private(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def write_service(
    path: Path,
    binary: Path,
    config: Path,
    data: Path,
    projects: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = f"""[Unit]
Description=Hermes Local Files Syncthing
After=network-online.target
Wants=network-online.target

[Service]
ExecStart={binary} serve --no-browser --no-port-probing --no-restart --no-upgrade -C {config} -D {data}
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={config} {data} {projects}

[Install]
WantedBy=default.target
"""
    temporary = path.with_suffix(".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-root",
        type=Path,
        required=True,
        help="Private storage root below /srv",
    )
    args = parser.parse_args()
    if os.uname().sysname != "Linux" or os.uname().machine != "x86_64":
        raise SystemExit("This pinned installer currently supports Linux x86_64 only")

    root = args.data_root.expanduser().resolve()
    if Path("/srv") not in root.parents:
        raise SystemExit("Data root must be below /srv")
    config_dir = root / "syncthing/config"
    data_dir = root / "syncthing/data"
    projects_dir = root / "projects"
    for directory in (config_dir, data_dir, projects_dir):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    install_dir = Path.home() / ".local/lib/hermes-local-files"
    binary = install_binary(install_dir)

    config_xml = config_dir / "config.xml"
    if not config_xml.exists():
        run(
            str(binary),
            "generate",
            "--no-port-probing",
            "-C", str(config_dir),
            "-D", str(data_dir),
        )
    api_key = configure_xml(config_xml)
    write_json_private(
        Path.home() / ".config/hermes-local-files/server.json",
        {
            "projects_root": str(projects_dir),
            "syncthing_api_url": "http://127.0.0.1:8384",
            "syncthing_api_key": api_key,
        },
    )

    unit = Path.home() / ".config/systemd/user/hermes-local-files-syncthing.service"
    write_service(unit, binary, config_dir, data_dir, projects_dir)
    run("systemctl", "--user", "daemon-reload")
    run("systemctl", "--user", "enable", "--now", unit.name)
    run("systemctl", "--user", "is-active", "--quiet", unit.name)
    print(f"Installed Syncthing {VERSION}; project data: {projects_dir}")


if __name__ == "__main__":
    main()
