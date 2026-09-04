import re
import unittest
from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).parents[1]
SKILL = ROOT / "skills/hermes-local-files-operations/SKILL.md"


class DocumentationContractTests(unittest.TestCase):
    def test_skill_frontmatter_and_sections_follow_contract(self):
        content = SKILL.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("---\n"))
        _, frontmatter, body = content.split("---\n", 2)
        fields = {}
        for line in frontmatter.splitlines():
            if ":" in line and not line.startswith(" "):
                key, value = line.split(":", 1)
                fields[key] = value.strip()

        self.assertEqual(fields["name"], "hermes-local-files-operations")
        self.assertLessEqual(len(fields["description"]), 60)
        self.assertTrue(fields["description"].endswith("."))
        self.assertIn("Joskmo", fields["author"])
        self.assertIn("Hermes Agent", fields["author"])
        self.assertEqual(fields["platforms"], "[linux, macos]")
        for heading in (
            "## When to use",
            "## Prerequisites",
            "## How to run",
            "## Procedure",
            "## Pitfalls",
            "## Verification",
        ):
            self.assertIn(heading, body)
        self.assertNotIn("/Users/", content)
        private_key_marker = "BEGIN " + "OPENSSH PRIVATE KEY"
        self.assertNotIn(private_key_marker, content)

    def test_readme_local_links_and_images_exist(self):
        for name in ("README.md", "README.ru.md"):
            readme = (ROOT / name).read_text(encoding="utf-8")
            links = re.findall(
                r"(?:\[[^]]*\]\(|<img\s+src=\")([^\" )#]+)",
                readme,
            )
            local = [
                link for link in links
                if "://" not in link and not link.startswith("mailto:")
            ]

            self.assertGreaterEqual(len(local), 5)
            for link in local:
                with self.subTest(readme=name, link=link):
                    self.assertTrue((ROOT / link).exists())

    def test_readmes_have_matching_language_switches_and_install_paths(self):
        english = (ROOT / "README.md").read_text(encoding="utf-8")
        russian = (ROOT / "README.ru.md").read_text(encoding="utf-8")

        self.assertIn("**English** | [Русский](README.ru.md)", english)
        self.assertIn("[English](README.md) | **Русский**", russian)
        self.assertIn("## What the user sees", english)
        self.assertIn("### Option 1: delegate installation to an AI agent", english)
        self.assertIn("### Option 2: install manually", english)
        self.assertIn("## Как это выглядит для пользователя", russian)
        self.assertIn("### Вариант 1: поручить установку ИИ-агенту", russian)
        self.assertIn("### Вариант 2: установить вручную", russian)
        for command in (
            "./bin/hermes-local-files install-server",
            "./bin/hermes-local-files install-macos",
            "./bin/hermes-local-files doctor --scope server --json",
            "./bin/hermes-local-files doctor --scope macos --json",
        ):
            with self.subTest(command=command):
                self.assertIn(command, english)
                self.assertIn(command, russian)
        self.assertGreater(len(re.findall(r"[А-Яа-яЁё]", russian)), 1000)

    def test_agent_document_uses_machine_readable_acceptance_contract(self):
        content = (ROOT / "docs/agent-installation.md").read_text(encoding="utf-8")

        self.assertIn('"schema_version": 1', content)
        self.assertIn("doctor --scope server --json", content)
        self.assertIn("doctor --scope macos --json", content)
        self.assertIn("Mac → server", content)
        self.assertIn("Server → Mac", content)
        self.assertIn("E2E ready", content)

    def test_readme_svgs_are_valid_and_accessible(self):
        for name in (
            "user-flow.en.svg",
            "user-flow.ru.svg",
            "architecture.en.svg",
            "architecture.ru.svg",
        ):
            with self.subTest(name=name):
                root = ElementTree.parse(ROOT / "assets" / name).getroot()
                namespace = "{http://www.w3.org/2000/svg}"
                self.assertIsNotNone(root.find(f"{namespace}title"))
                self.assertIsNotNone(root.find(f"{namespace}desc"))

    def test_public_tree_has_no_private_deployment_identifiers(self):
        prohibited = (
            "Da" + "ria",
            "Да" + "ша",
            "jos" + "-dev.ru",
            "self-hosted" + "-music",
            "iXuF" + "L2eySR25",
            "/Users/" + "da" + "ria",
        )
        for path in ROOT.rglob("*"):
            if not path.is_file() or ".git" in path.parts:
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for value in prohibited:
                with self.subTest(path=path.relative_to(ROOT), value=value):
                    self.assertNotIn(value.casefold(), content.casefold())


if __name__ == "__main__":
    unittest.main()
