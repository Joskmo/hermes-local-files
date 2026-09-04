import base64
from hashlib import sha256
import io
import tempfile
import unittest
from pathlib import Path
import tarfile
from types import SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree

from scripts.install_macos import (
    configure_syncthing,
    install_desktop_plugin,
    launch_payload,
    pin_host_key,
)
from scripts.install_server import configure_xml, install_binary, safe_extract, write_service


def sample_config(path: Path) -> None:
    root = ElementTree.Element("configuration")
    gui = ElementTree.SubElement(root, "gui")
    ElementTree.SubElement(gui, "address").text = "0.0.0.0:8384"
    ElementTree.SubElement(gui, "apikey").text = "generated-api-key"
    options = ElementTree.SubElement(root, "options")
    values = {
        "listenAddress": "default",
        "globalAnnounceEnabled": "true",
        "localAnnounceEnabled": "true",
        "relaysEnabled": "true",
        "natEnabled": "true",
        "startBrowser": "true",
    }
    for name, value in values.items():
        ElementTree.SubElement(options, name).text = value
    ElementTree.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


class InstallerTests(unittest.TestCase):
    def assert_private_syncthing_config(self, path, configure):
        sample_config(path)

        self.assertEqual(configure(path), "generated-api-key")

        root = ElementTree.parse(path).getroot()
        self.assertEqual(root.findtext("gui/address"), "127.0.0.1:8384")
        self.assertEqual(root.findtext("options/listenAddress"), "tcp://127.0.0.1:22000")
        for field in (
            "globalAnnounceEnabled",
            "localAnnounceEnabled",
            "relaysEnabled",
            "natEnabled",
            "startBrowser",
        ):
            self.assertEqual(root.findtext(f"options/{field}"), "false")
        self.assertEqual(path.stat().st_mode & 0o077, 0)

    def test_server_xml_is_loopback_only(self):
        with tempfile.TemporaryDirectory() as raw:
            self.assert_private_syncthing_config(Path(raw) / "config.xml", configure_xml)

    def test_macos_xml_is_loopback_only(self):
        with tempfile.TemporaryDirectory() as raw:
            self.assert_private_syncthing_config(
                Path(raw) / "config.xml",
                configure_syncthing,
            )

    def test_desktop_install_injects_token_and_connection_privately(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source/desktop/plugin.js"
            source.parent.mkdir(parents=True)
            source.write_text(
                "const TOKEN = '__COMPANION_TOKEN__'\n"
                "const CONNECTION = '__TARGET_CONNECTION_ID__'\n"
                "const PROFILE = '__TARGET_PROFILE__'\n",
                encoding="utf-8",
            )

            install_desktop_plugin(
                root / "source",
                root / "hermes",
                "token-1",
                "server-connection",
                "work",
            )

            installed = root / "hermes/desktop-plugins/local-files/plugin.js"
            text = installed.read_text(encoding="utf-8")
            self.assertIn('"token-1"', text)
            self.assertIn('"server-connection"', text)
            self.assertIn('"work"', text)
            self.assertNotIn("__COMPANION_TOKEN__", text)
            self.assertNotIn("__TARGET_CONNECTION_ID__", text)
            self.assertNotIn("__TARGET_PROFILE__", text)
            self.assertEqual(installed.stat().st_mode & 0o777, 0o600)

    def test_launch_payload_uses_argument_array_and_keepalive(self):
        payload = launch_payload("example", ["/bin/example", "--flag"], Path("/tmp"))

        self.assertEqual(payload["ProgramArguments"], ["/bin/example", "--flag"])
        self.assertTrue(payload["KeepAlive"])
        self.assertTrue(payload["RunAtLoad"])

    def test_host_key_is_pinned_before_known_hosts_is_written(self):
        blob = b"synthetic-ed25519-public-key-blob"
        encoded = base64.b64encode(blob).decode()
        fingerprint = "SHA256:" + base64.b64encode(sha256(blob).digest()).decode().rstrip("=")
        scan = SimpleNamespace(
            returncode=0,
            stdout=f"[example.test]:6000 ssh-ed25519 {encoded}\n",
        )
        with tempfile.TemporaryDirectory() as raw, patch(
            "scripts.install_macos.subprocess.run",
            return_value=scan,
        ):
            destination = Path(raw) / "known_hosts"
            pin_host_key("user@example.test", 6000, fingerprint, destination)

            self.assertEqual(
                destination.read_text(encoding="utf-8"),
                f"[example.test]:6000 ssh-ed25519 {encoded}\n",
            )
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            with self.assertRaisesRegex(RuntimeError, "host key mismatch"):
                pin_host_key("user@example.test", 6000, "SHA256:wrong", destination)

    def test_server_archive_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            archive = root / "bad.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                member = tarfile.TarInfo("../../outside")
                payload = b"unsafe"
                member.size = len(payload)
                bundle.addfile(member, io.BytesIO(payload))

            with self.assertRaisesRegex(RuntimeError, "unsafe path"):
                safe_extract(archive, root / "extract")
            self.assertFalse((root.parent / "outside").exists())

    def test_server_installer_replaces_existing_binary_from_verified_asset(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            install = root / "install"
            install.mkdir()
            existing = install / "syncthing-2.1.3"
            existing.write_bytes(b"tampered")
            trusted = root / "trusted-syncthing"
            trusted.write_bytes(b"verified-binary")

            with patch("scripts.install_server.download_verified") as download, patch(
                "scripts.install_server.safe_extract",
                return_value=trusted,
            ):
                result = install_binary(install)

            download.assert_called_once()
            self.assertEqual(result, existing)
            self.assertEqual(existing.read_bytes(), b"verified-binary")

    def test_server_service_can_write_project_root(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            unit = root / "service"
            write_service(
                unit,
                root / "syncthing",
                root / "config",
                root / "data",
                root / "projects",
            )

            content = unit.read_text(encoding="utf-8")
            self.assertIn(
                f"ReadWritePaths={root / 'config'} {root / 'data'} {root / 'projects'}",
                content,
            )


if __name__ == "__main__":
    unittest.main()
