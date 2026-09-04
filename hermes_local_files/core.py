"""Общие безопасные примитивы Hermes Local Files."""

from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
import re
from typing import Any, Dict, List


_TRANSLITERATION = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
        "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k",
        "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
        "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
        "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "",
        "э": "e", "ю": "iu", "я": "ia",
    }
)
_CONFLICT_RE = re.compile(r"\.sync-conflict-[^/]+", re.IGNORECASE)


@dataclass(frozen=True)
class ProjectMapping:
    """Связь между локальной папкой и серверной копией проекта."""

    mapping_id: str
    name: str
    local_path: str
    folder_id: str
    server_path: str

    def to_dict(self) -> Dict[str, str]:
        """Вернуть только несекретные данные связи."""

        return asdict(self)


def _slugify(value: str) -> str:
    normalized = value.strip().lower().translate(_TRANSLITERATION)
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    return slug


def safe_project_path(root: Path, name: str) -> Path:
    """Построить путь проекта строго внутри заданного корня."""

    raw = name.strip()
    if not raw or raw in {".", ".."} or "/" in raw or "\\" in raw:
        raise ValueError("Project name must be a plain non-empty name")
    slug = _slugify(raw)
    if not slug:
        raise ValueError("Project name does not contain usable characters")
    root = root.resolve()
    candidate = (root / slug).resolve()
    if candidate.parent != root:
        raise ValueError("Project path escapes the configured root")
    return candidate


def stable_folder_id(mapping_id: str) -> str:
    """Получить стабильный непрозрачный идентификатор Syncthing."""

    if not mapping_id.strip():
        raise ValueError("Mapping id is required")
    digest = sha256(mapping_id.encode("utf-8")).hexdigest()[:20]
    return f"hermes-{digest}"


def build_folder_config(
    *,
    folder_id: str,
    label: str,
    path: str,
    remote_device_id: str,
    server: bool,
) -> Dict[str, Any]:
    """Собрать совместимую конфигурацию общей папки Syncthing."""

    versioning = {
        "type": "staggered" if server else "",
        "params": (
            {
                "cleanoutDays": "365",
                "maxAge": "7776000",
            }
            if server
            else {}
        ),
        "cleanupIntervalS": 3600,
        "fsPath": ".stversions" if server else "",
        "fsType": "basic",
    }
    return {
        "id": folder_id,
        "label": label,
        "filesystemType": "basic",
        "path": path,
        "type": "sendreceive",
        "devices": [{"deviceID": remote_device_id}],
        "rescanIntervalS": 300,
        "fsWatcherEnabled": True,
        "fsWatcherDelayS": 2,
        "ignorePerms": True,
        "autoNormalize": True,
        "minDiskFree": {"value": 2, "unit": "%"},
        "versioning": versioning,
        "maxConflicts": 10,
        "paused": False,
    }


def reduce_sync_status(
    *,
    tunnel_up: bool,
    connected: bool,
    local_state: str,
    remote_state: str,
    local_completion: float,
    remote_completion: float,
    local_errors: int,
    remote_errors: int,
    conflicts: int,
) -> str:
    """Свести технические показатели к четырём понятным состояниям UI."""

    if conflicts or local_errors or remote_errors:
        return "attention"
    if not tunnel_up or not connected:
        return "offline"
    complete = local_completion >= 100.0 and remote_completion >= 100.0
    idle = local_state == "idle" and remote_state == "idle"
    return "synced" if complete and idle else "syncing"


def reduce_two_sided_status(
    local: Dict[str, Any],
    remote: Dict[str, Any],
    *,
    conflicts: int,
) -> str:
    """Свести два реальных Syncthing snapshot без синтетических нулей."""

    if conflicts:
        return "attention"
    snapshots = (local, remote)
    if any(
        int(snapshot.get("pull_errors") or 0)
        or int(snapshot.get("folder_errors") or 0)
        for snapshot in snapshots
    ):
        return "attention"
    if any(not bool(snapshot.get("connected")) for snapshot in snapshots):
        return "offline"
    remote_states = {str(snapshot.get("remote_state") or "unknown") for snapshot in snapshots}
    if remote_states != {"valid"}:
        return "attention" if remote_states & {"paused", "notSharing"} else "offline"
    complete = all(
        float(snapshot.get("local_completion") or 0.0) >= 100.0
        and float(snapshot.get("remote_completion") or 0.0) >= 100.0
        for snapshot in snapshots
    )
    idle = all(str(snapshot.get("state") or "unknown") == "idle" for snapshot in snapshots)
    return "synced" if complete and idle else "syncing"


def conflict_paths(root: Path) -> List[Path]:
    """Найти conflict-копии, не читая содержимое пользовательских файлов."""

    if not root.is_dir():
        return []
    return sorted(
        (path for path in root.rglob("*") if path.is_file() and _CONFLICT_RE.search(path.name)),
        key=lambda path: str(path),
    )
