#!/usr/bin/env python3
"""Собрать один uncompiled ESM-файл Desktop plugin без относительных импортов."""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / "plugins/local-files/desktop/workflow.mjs"
TEMPLATE = ROOT / "plugins/local-files/desktop/plugin.template.js"
OUTPUT = ROOT / "desktop/plugin.js"
MARKER = "/* __WORKFLOW__ */"


def main() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8").replace("export ", "")
    template = TEMPLATE.read_text(encoding="utf-8")
    if template.count(MARKER) != 1:
        raise SystemExit("Desktop plugin template must contain one workflow marker")
    output = template.replace(MARKER, workflow.rstrip())
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(output.rstrip() + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
