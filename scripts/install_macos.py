#!/usr/bin/env python3
"""Установить Local Files Desktop plugin и фоновые службы на macOS."""

from __future__ import annotations

import argparse
import base64
from hashlib import sha256
import json
import os
from pathlib import Path
import plistlib
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import time
from typing import Optional
from urllib.request import Request, urlopen
from xml.etree import ElementTree

SOURCE_ROOT = Path(__file__).resolve().parent.parent
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from hermes_local_files.companion import TunnelCommand


LABEL_PREFIX = "app.hermes.local-files"
_SSH_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+$")


def run(
    *argv: str,
    check: bool = True,
    input_text: Optional[str] = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(argv),
        check=check,
        input=input_text,
        text=True,
    )


def syncthing_binary() -> Path:
    existing = shutil.which("syncthing")
    if existing:
        return Path(existing).resolve()
    brew = shutil.which("brew")
    if not brew:
        raise RuntimeError("Homebrew is required to install Syncthing")
    run(brew, "install", "syncthing")
    prefix = subprocess.check_output([brew, "--prefix", "syncthing"], text=True).strip()
    binary = Path(prefix) / "bin/syncthing"
    if not binary.is_file():
        raise RuntimeError("Homebrew installed Syncthing but its binary was not found")
    return binary.resolve()


def set_text(root: ElementTree.Element, path: str, value: str) -> None:
    node = root.find(path)
    if node is None:
        raise RuntimeError(f"Syncthing config is missing {path}")
    node.text = value


def configure_syncthing(path: Path) -> str:
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


def write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def write_plist(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".plist.tmp")
    with temporary.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=True)
    os.replace(temporary, path)


def launch_payload(label: str, argv: list[str], logs: Path, **extra) -> dict:
    return {
        "Label": label,
        "ProgramArguments": argv,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "ThrottleInterval": 5,
        "StandardOutPath": str(logs / f"{label}.log"),
        "StandardErrorPath": str(logs / f"{label}.error.log"),
        **extra,
    }


def pin_host_key(target: str, port: int, expected: str, destination: Path) -> None:
    """Сверить ED25519 key с pinned fingerprint и создать isolated known_hosts."""

    host = target.rsplit("@", 1)[-1]
    if not _SSH_HOST_RE.fullmatch(host):
        raise ValueError("Unsafe SSH host")
    scan = subprocess.run(
        ["/usr/bin/ssh-keyscan", "-T", "10", "-t", "ed25519", "-p", str(port), host],
        check=False,
        capture_output=True,
        text=True,
    )
    records = [line.split() for line in scan.stdout.splitlines() if not line.startswith("#")]
    records = [parts for parts in records if len(parts) >= 3 and parts[1] == "ssh-ed25519"]
    if scan.returncode or len(records) != 1:
        raise RuntimeError("Could not read the server ED25519 host key")
    try:
        blob = base64.b64decode(records[0][2], validate=True)
    except ValueError as exc:
        raise RuntimeError("Server returned an invalid SSH host key") from exc
    actual = "SHA256:" + base64.b64encode(sha256(blob).digest()).decode().rstrip("=")
    if actual != expected:
        raise RuntimeError(f"SSH host key mismatch: expected {expected}, received {actual}")
    write_private(destination, f"[{host}]:{port} ssh-ed25519 {records[0][2]}\n")


def install_restricted_key(
    target: str,
    port: int,
    key: Path,
    known_hosts: Path,
) -> None:
    key.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not key.exists():
        run(
            "/usr/bin/ssh-keygen",
            "-q", "-t", "ed25519", "-N", "", "-C", "hermes-local-files",
            "-f", str(key),
        )
    public = key.with_suffix(key.suffix + ".pub").read_text(encoding="utf-8").strip()
    restricted = (
        'restrict,port-forwarding,command="/usr/bin/false",'
        'permitopen="127.0.0.1:22000" '
        f"{public}"
    )
    bootstrap = [
        "/usr/bin/ssh",
        "-p", str(port),
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={known_hosts}",
        "-o", "GlobalKnownHostsFile=/dev/null",
        target,
    ]
    remote_python = """
import os
from pathlib import Path
import sys
line = sys.stdin.read().strip()
root = Path.home() / '.ssh'
root.mkdir(mode=0o700, exist_ok=True)
target = root / 'authorized_keys'
existing = target.read_text(encoding='utf-8').splitlines() if target.exists() else []
existing = [item for item in existing if not item.rstrip().endswith(' hermes-local-files')]
existing.append(line)
target.write_text('\\n'.join(existing) + '\\n', encoding='utf-8')
target.chmod(0o600)
""".strip()
    command = "python3 -c " + shlex.quote(remote_python)
    run(*bootstrap, command, input_text=restricted + "\n")
    probe = run(
        "/usr/bin/ssh",
        "-T",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={known_hosts}",
        "-o", "GlobalKnownHostsFile=/dev/null",
        "-o", "IdentitiesOnly=yes",
        "-i", str(key),
        "-p", str(port),
        target,
        check=False,
    )
    if probe.returncode != 1:
        raise RuntimeError("Restricted SSH key verification failed")


def install_desktop_plugin(
    source_root: Path,
    hermes_home: Path,
    token: str,
    connection_id: str,
    profile: str,
) -> None:
    source = (source_root / "desktop/plugin.js").read_text(encoding="utf-8")
    if source.count("'__COMPANION_TOKEN__'") != 1:
        raise RuntimeError("Desktop plugin token placeholder is missing or duplicated")
    if source.count("'__TARGET_CONNECTION_ID__'") != 1:
        raise RuntimeError("Desktop plugin connection placeholder is missing or duplicated")
    if source.count("'__TARGET_PROFILE__'") != 1:
        raise RuntimeError("Desktop plugin profile placeholder is missing or duplicated")
    source = source.replace("'__COMPANION_TOKEN__'", json.dumps(token))
    source = source.replace("'__TARGET_CONNECTION_ID__'", json.dumps(connection_id))
    source = source.replace("'__TARGET_PROFILE__'", json.dumps(profile))
    destination = hermes_home / "desktop-plugins/local-files/plugin.js"
    write_private(destination, source)


def restart_agents(plists: list[Path]) -> None:
    domain = f"gui/{os.getuid()}"
    for plist in plists:
        run("/bin/launchctl", "bootout", domain, str(plist), check=False)
    for plist in plists:
        run("/bin/launchctl", "bootstrap", domain, str(plist))


def wait_for_companion(token: str) -> None:
    deadline = time.monotonic() + 30
    request = Request(
        "http://127.0.0.1:45671/v1/health",
        headers={"X-Hermes-Local-Files-Token": token},
    )
    while time.monotonic() < deadline:
        try:
            with urlopen(request, timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(1)
    raise RuntimeError("Local Files companion did not become healthy")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hermes-home", type=Path, default=Path.home() / ".hermes")
    parser.add_argument("--ssh-target", required=True, help="SSH destination: user@host")
    parser.add_argument("--ssh-port", type=int, default=22)
    parser.add_argument("--host-key-sha256", required=True, help="Pinned ED25519 SHA256 fingerprint")
    parser.add_argument("--connection-id", required=True, help="Hermes Desktop SSH connection ID")
    parser.add_argument("--profile", required=True, help="Remote Hermes profile name")
    args = parser.parse_args()
    if sys.platform != "darwin":
        raise SystemExit("This installer must run on macOS")

    source_root = Path(__file__).resolve().parent.parent
    binary = syncthing_binary()
    app = Path.home() / "Library/Application Support/Hermes Local Files"
    config_dir = app / "syncthing/config"
    data_dir = app / "syncthing/data"
    logs = app / "logs"
    for directory in (config_dir, data_dir, logs):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    config_xml = config_dir / "config.xml"
    if not config_xml.exists():
        run(
            str(binary), "generate", "--no-port-probing",
            "-C", str(config_dir), "-D", str(data_dir),
        )
    api_key = configure_syncthing(config_xml)

    key = app / "ssh/hermes-local-files"
    known_hosts = app / "ssh/known_hosts"
    pin_host_key(
        args.ssh_target,
        args.ssh_port,
        args.host_key_sha256,
        known_hosts,
    )
    install_restricted_key(args.ssh_target, args.ssh_port, key, known_hosts)
    token = secrets.token_urlsafe(32)
    config = {
        "token": token,
        "syncthing_api_key": api_key,
        "syncthing_api_url": "http://127.0.0.1:8384",
        "tunnel_port": 22001,
        "companion_port": 45671,
        "mapping_path": str(app / "mappings.json"),
    }
    config_path = app / "config.json"
    write_private(config_path, json.dumps(config, indent=2) + "\n")

    library = app / "lib"
    package_target = library / "hermes_local_files"
    if package_target.exists():
        shutil.rmtree(package_target)
    library.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copytree(
        source_root / "hermes_local_files",
        package_target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    install_desktop_plugin(
        source_root,
        args.hermes_home.expanduser().resolve(),
        token,
        args.connection_id,
        args.profile,
    )

    agents = Path.home() / "Library/LaunchAgents"
    syncthing_plist = agents / f"{LABEL_PREFIX}.syncthing.plist"
    tunnel_plist = agents / f"{LABEL_PREFIX}.tunnel.plist"
    companion_plist = agents / f"{LABEL_PREFIX}.companion.plist"
    write_plist(
        syncthing_plist,
        launch_payload(
            f"{LABEL_PREFIX}.syncthing",
            [
                str(binary), "serve", "--no-browser", "--no-port-probing",
                "--no-restart", "--no-upgrade",
                "-C", str(config_dir), "-D", str(data_dir),
            ],
            logs,
        ),
    )
    tunnel = TunnelCommand(
        args.ssh_target,
        local_port=22001,
        remote_port=22000,
        ssh_port=args.ssh_port,
        identity_file=key,
        known_hosts_file=known_hosts,
    ).argv()
    write_plist(
        tunnel_plist,
        launch_payload(f"{LABEL_PREFIX}.tunnel", tunnel, logs),
    )
    write_plist(
        companion_plist,
        launch_payload(
            f"{LABEL_PREFIX}.companion",
            [sys.executable, "-m", "hermes_local_files.cli", "--config", str(config_path)],
            logs,
            EnvironmentVariables={"PYTHONPATH": str(library)},
            WorkingDirectory=str(app),
        ),
    )
    restart_agents([syncthing_plist, tunnel_plist, companion_plist])
    wait_for_companion(token)
    print("Hermes Local Files is installed and running. Restart Hermes Desktop once.")


if __name__ == "__main__":
    main()
