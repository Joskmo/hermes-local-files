import tempfile
import unittest
from pathlib import Path

from hermes_local_files.core import (
    ProjectMapping,
    build_folder_config,
    conflict_paths,
    reduce_sync_status,
    reduce_two_sided_status,
    safe_project_path,
    stable_folder_id,
)


class CoreContractTests(unittest.TestCase):
    def test_safe_project_path_stays_inside_root(self):
        root = Path("/srv/hermes-local-files/work/projects")

        path = safe_project_path(root, " Семейный бюджет 2026 ")

        self.assertEqual(path, root / "semeinyi-biudzhet-2026")

    def test_safe_project_path_rejects_empty_or_traversal_name(self):
        root = Path("/srv/hermes-local-files/work/projects")

        for value in ("", "..", "../secret", "/tmp/escape"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                safe_project_path(root, value)

    def test_folder_id_is_stable_without_leaking_local_path(self):
        first = stable_folder_id("mapping-123")
        second = stable_folder_id("mapping-123")

        self.assertEqual(first, second)
        self.assertRegex(first, r"^hermes-[a-f0-9]{20}$")
        self.assertNotIn("mapping", first)

    def test_folder_config_is_bidirectional_watched_and_versioned(self):
        config = build_folder_config(
            folder_id="hermes-deadbeef",
            label="Photos",
            path="/srv/hermes-local-files/work/projects/photos",
            remote_device_id="AAAA-BBBB",
            server=True,
        )

        self.assertEqual(config["type"], "sendreceive")
        self.assertTrue(config["fsWatcherEnabled"])
        self.assertEqual(config["devices"], [{"deviceID": "AAAA-BBBB"}])
        self.assertEqual(config["versioning"]["type"], "staggered")
        self.assertEqual(config["versioning"]["params"]["maxAge"], "7776000")
        self.assertEqual(config["versioning"]["fsPath"], ".stversions")

    def test_status_is_synced_only_when_every_boundary_is_healthy(self):
        healthy = reduce_sync_status(
            tunnel_up=True,
            connected=True,
            local_state="idle",
            remote_state="idle",
            local_completion=100.0,
            remote_completion=100.0,
            local_errors=0,
            remote_errors=0,
            conflicts=0,
        )
        syncing = reduce_sync_status(
            tunnel_up=True,
            connected=True,
            local_state="syncing",
            remote_state="idle",
            local_completion=84.0,
            remote_completion=100.0,
            local_errors=0,
            remote_errors=0,
            conflicts=0,
        )
        conflict = reduce_sync_status(
            tunnel_up=True,
            connected=True,
            local_state="idle",
            remote_state="idle",
            local_completion=100.0,
            remote_completion=100.0,
            local_errors=0,
            remote_errors=0,
            conflicts=1,
        )
        offline = reduce_sync_status(
            tunnel_up=False,
            connected=False,
            local_state="idle",
            remote_state="idle",
            local_completion=100.0,
            remote_completion=100.0,
            local_errors=0,
            remote_errors=0,
            conflicts=0,
        )

        self.assertEqual(healthy, "synced")
        self.assertEqual(syncing, "syncing")
        self.assertEqual(conflict, "attention")
        self.assertEqual(offline, "offline")

    def test_two_sided_status_requires_real_healthy_peer_snapshots(self):
        healthy = {
            "state": "idle",
            "pull_errors": 0,
            "folder_errors": 0,
            "local_completion": 100.0,
            "remote_completion": 100.0,
            "remote_state": "valid",
            "connected": True,
        }

        self.assertEqual(
            reduce_two_sided_status(healthy, healthy, conflicts=0),
            "synced",
        )
        for field, value in (
            ("pull_errors", 1),
            ("folder_errors", 1),
            ("local_completion", 99.9),
            ("remote_completion", 99.9),
            ("remote_state", "paused"),
            ("connected", False),
        ):
            broken = {**healthy, field: value}
            with self.subTest(field=field):
                self.assertNotEqual(
                    reduce_two_sided_status(healthy, broken, conflicts=0),
                    "synced",
                )

    def test_conflicts_are_detected_without_reading_contents(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "normal.txt").write_text("normal")
            (root / "report.sync-conflict-20260904-001122-ABCDEF.docx").write_text("conflict")

            found = conflict_paths(root)

        self.assertEqual([path.name for path in found], [
            "report.sync-conflict-20260904-001122-ABCDEF.docx"
        ])

    def test_mapping_serialization_excludes_api_credentials(self):
        mapping = ProjectMapping(
            mapping_id="map-1",
            name="Family",
            local_path="/Users/example/Family",
            folder_id="hermes-abc",
            server_path="/srv/hermes-local-files/work/projects/family",
        )

        payload = mapping.to_dict()

        self.assertEqual(payload["mapping_id"], "map-1")
        self.assertNotIn("api_key", payload)
        self.assertNotIn("ssh_key", payload)


if __name__ == "__main__":
    unittest.main()
