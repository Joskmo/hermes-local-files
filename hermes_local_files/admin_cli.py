"""Agent-friendly administration CLI for Hermes Local Files."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import plistlib
import socket
import subprocess
import sys
from typing import Any, Dict, List, Optional, Sequence
from urllib.request import Request, urlopen
from xml.etree import ElementTree


VERSION = "0.1.0"
_SCHEMA_VERSION = 1
_LABELS = (
    "app.hermes.local-files.syncthing",
    "app.hermes.local-files.tunnel",
    "app.hermes.local-files.companion",
)


@dataclass(frozen=True)
class Check:
    """One stable, machine-readable health assertion."""

    id: str
    ok: bool
    detail: str
    remedy: str


def _private_mode(path: Path) -> bool:
    try:
        return path.is_file() and not (path.stat().st_mode & 0o077)
    except OSError:
        return False


def _read_private_json(path: Path) -> Optional[Dict[str, Any]]:
    if not _private_mode(path):
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _syncthing_xml_check(path: Path) -> Check:
    try:
        root = ElementTree.parse(path).getroot()
        values = {
            "gui": root.findtext("gui/address"),
            "listen": root.findtext("options/listenAddress"),
            "global": root.findtext("options/globalAnnounceEnabled"),
            "local": root.findtext("options/localAnnounceEnabled"),
            "relay": root.findtext("options/relaysEnabled"),
            "nat": root.findtext("options/natEnabled"),
        }
    except (OSError, ElementTree.ParseError):
        return Check(
            "syncthing-network",
            False,
            "configuration unavailable",
            "Run the matching install command again.",
        )
    expected = {
        "gui": "127.0.0.1:8384",
        "listen": "tcp://127.0.0.1:22000",
        "global": "false",
        "local": "false",
        "relay": "false",
        "nat": "false",
    }
    ok = values == expected
    return Check(
        "syncthing-network",
        ok,
        "loopback-only; discovery, relay, and NAT disabled" if ok else "unsafe network settings",
        "Run the matching install command again." if not ok else "",
    )


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _local_request(config: Dict[str, Any], path: str) -> Optional[Dict[str, Any]]:
    token = str(config.get("token") or "")
    if not token:
        return None
    request = Request(
        f"http://127.0.0.1:45671{path}",
        headers={"X-Hermes-Local-Files-Token": token},
    )
    try:
        with urlopen(request, timeout=2) as response:
            payload = json.loads(response.read())
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _macos_checks() -> List[Check]:
    app = Path.home() / "Library/Application Support/Hermes Local Files"
    config_path = app / "config.json"
    config = _read_private_json(config_path)
    checks = [
        Check(
            "private-config",
            config is not None,
            "owner-only configuration" if config else "missing, invalid, or not mode 0600",
            "Run hermes-local-files install-macos.",
        )
    ]
    checks.append(_syncthing_xml_check(app / "syncthing/config/config.xml"))

    domain = f"gui/{os.getuid()}"
    active = []
    for label in _LABELS:
        result = subprocess.run(
            ["/bin/launchctl", "print", f"{domain}/{label}"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            active.append(label)
    checks.append(Check(
        "launch-agents",
        len(active) == len(_LABELS),
        f"{len(active)}/{len(_LABELS)} loaded",
        "Log out and in, or run hermes-local-files install-macos again.",
    ))

    health = _local_request(config or {}, "/v1/health")
    checks.append(Check(
        "companion",
        health == {"ok": True},
        "healthy" if health == {"ok": True} else "unreachable or unauthorized",
        "Inspect companion LaunchAgent logs, then reinstall if needed.",
    ))
    tunnel = _port_open(22001)
    checks.append(Check(
        "ssh-tunnel",
        tunnel,
        "127.0.0.1:22001 is listening" if tunnel else "not listening",
        "Check SSH reachability and the tunnel LaunchAgent log.",
    ))

    hermes_home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    plugin = hermes_home / "desktop-plugins/local-files/plugin.js"
    try:
        source = plugin.read_text(encoding="utf-8")
        installed = (
            "__COMPANION_TOKEN__" not in source
            and "__HOME_CONNECTION_ID__" not in source
            and _private_mode(plugin)
        )
    except OSError:
        installed = False
    checks.append(Check(
        "desktop-plugin",
        installed,
        "installed with injected local configuration" if installed else "missing or incomplete",
        "Pass the correct --hermes-home to install-macos.",
    ))

    projects = _local_request(config or {}, "/v1/projects")
    count = len(projects.get("projects", [])) if projects else 0
    checks.append(Check(
        "project-inventory",
        projects is not None,
        f"{count} synchronized project(s)" if projects is not None else "unavailable",
        "Restore the companion before diagnosing projects.",
    ))
    return checks


def _server_checks() -> List[Check]:
    config_path = Path.home() / ".config/hermes-local-files/server.json"
    config = _read_private_json(config_path)
    checks = [Check(
        "private-config",
        config is not None,
        "owner-only configuration" if config else "missing, invalid, or not mode 0600",
        "Run hermes-local-files install-server.",
    )]
    root = Path(str((config or {}).get("projects_root") or "/missing"))
    writable = root.is_dir() and os.access(root, os.W_OK)
    checks.append(Check(
        "projects-root",
        writable,
        str(root) if writable else "missing or not writable",
        "Create the configured /srv project directory for the service user.",
    ))

    service = subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", "hermes-local-files-syncthing.service"],
        check=False,
    )
    checks.append(Check(
        "syncthing-service",
        service.returncode == 0,
        "active" if service.returncode == 0 else "inactive",
        "Run systemctl --user restart hermes-local-files-syncthing.service.",
    ))
    data_root = root.parent if root.name == "projects" else root
    checks.append(_syncthing_xml_check(data_root / "syncthing/config/config.xml"))
    checks.append(Check(
        "syncthing-api",
        _port_open(8384),
        "127.0.0.1:8384 is listening" if _port_open(8384) else "not listening",
        "Inspect the Syncthing user-service journal.",
    ))
    plugin_root = Path(__file__).resolve().parent.parent
    backend = plugin_root / "dashboard/plugin_api.py"
    checks.append(Check(
        "backend-plugin",
        backend.is_file(),
        "dashboard API present" if backend.is_file() else "dashboard API missing",
        "Reinstall the Hermes plugin from its pinned Git commit.",
    ))
    return checks


def resolve_scope(scope: str) -> str:
    if scope != "auto":
        return scope
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "server"
    raise ValueError("Automatic scope detection supports macOS and Linux only")


def collect_checks(scope: str) -> List[Check]:
    """Collect all authoritative checks for one host role."""

    return _macos_checks() if scope == "macos" else _server_checks()


def _print_report(scope: str, checks: Sequence[Check], json_output: bool) -> int:
    ok = all(check.ok for check in checks)
    if json_output:
        print(json.dumps({
            "schema_version": _SCHEMA_VERSION,
            "ok": ok,
            "scope": scope,
            "checks": [asdict(check) for check in checks],
        }, ensure_ascii=False, sort_keys=True))
    else:
        for check in checks:
            print(f"[{'OK' if check.ok else 'FAIL'}] {check.id}: {check.detail}")
            if not check.ok and check.remedy:
                print(f"       fix: {check.remedy}")
        print("Healthy" if ok else "Problems found")
    return 0 if ok else 1


def _run_installer(command: str, extra: Sequence[str]) -> int:
    root = Path(__file__).resolve().parent.parent
    script = root / "scripts" / f"install_{command}.py"
    return subprocess.run([sys.executable, str(script), *extra], check=False).returncode


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] in {"install-server", "install-macos"}:
        role = args[0].removeprefix("install-")
        return _run_installer(role, args[1:])

    parser = argparse.ArgumentParser(prog="hermes-local-files")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("doctor", "status"):
        command = subparsers.add_parser(name)
        command.add_argument("--scope", choices=("auto", "server", "macos"), default="auto")
        command.add_argument("--json", action="store_true", dest="json_output")
    subparsers.add_parser("version")
    parsed = parser.parse_args(args)
    if parsed.command == "version":
        print(VERSION)
        return 0
    scope = resolve_scope(parsed.scope)
    return _print_report(scope, collect_checks(scope), parsed.json_output)


if __name__ == "__main__":
    raise SystemExit(main())
