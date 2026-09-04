#!/usr/bin/env python3
"""Build localized README SVGs from the neutral Russian source artwork."""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

TRANSLATIONS = {
    "user-flow.svg": {
        "Как пользоваться Hermes Local Files": "How to use Hermes Local Files",
        "Три шага: выбрать папку, дождаться синхронизации, работать с теми же файлами в Finder и Hermes.": (
            "Three steps: choose a folder, wait for synchronization, and work with the same files in Finder and Hermes."
        ),
        "Для пользователя это обычная папка на Mac": "To the user, this is a normal folder on the Mac",
        "Настройка нужна один раз. Дальше файлы синхронизируются автоматически.": (
            "Set it up once. Files synchronize automatically after that."
        ),
        "Выбрать папку": "Choose a folder",
        "Local Files → Добавить папку": "Local Files → Add folder",
        "Выберите папку и нажмите «Открыть»": "Select a folder and click Open",
        "Дождаться готовности": "Wait until ready",
        "Статус: «Синхронизация…»": "Status: Synchronizing…",
        "Готово: «Синхронизировано»": "Ready: Synchronized",
        "Работать как обычно": "Work normally",
        "Работайте с папкой в Finder": "Keep using the folder in Finder",
        "Правки Hermes сами появляются на Mac": "Hermes edits appear on the Mac",
    },
    "architecture.svg": {
        "Архитектура Hermes Local Files": "Hermes Local Files architecture",
        "Локальная папка и Syncthing на Mac соединяются с Syncthing и проектом Hermes на Debian через ограниченный SSH-туннель.": (
            "A local Mac folder connects to Syncthing and a Hermes project on Debian through a restricted SSH tunnel."
        ),
        "Что происходит под капотом": "What happens under the hood",
        "Hermes core не меняется. Синхронизация живёт отдельно и продолжает работать при закрытом Desktop.": (
            "Hermes core stays unchanged. Synchronization runs separately and continues while Desktop is closed."
        ),
        "MAC ПОЛЬЗОВАТЕЛЯ": "USER'S MAC",
        "УДАЛЁННЫЙ DEBIAN-СЕРВЕР": "REMOTE DEBIAN SERVER",
        "Mac → сервер": "Mac → server",
        "сервер → Mac": "server → Mac",
        "Выбор папки, статус, создание проекта": "Folder picker, status, and project creation",
        "127.0.0.1 · LaunchAgent · токен 0600": "127.0.0.1 · LaunchAgent · 0600 token",
        "Обычная папка в Finder": "Normal folder in Finder",
        "Файлы физически остаются на Mac": "Files remain physically on the Mac",
        "Проверяет путь и создаёт project mapping": "Validates the path and creates a project mapping",
        "Hermes project на /srv": "Hermes project under /srv",
        "С этой копией работает агент": "The agent works with this copy",
        "только 127.0.0.1:22000": "127.0.0.1:22000 only",
        "локальная часть": "local side",
        "серверная часть": "server side",
        "ограниченный SSH-канал": "restricted SSH channel",
        "данные проекта": "project data",
    },
}


def build() -> None:
    for source_name, translations in TRANSLATIONS.items():
        source = (ASSETS / source_name).read_text(encoding="utf-8")
        stem = Path(source_name).stem
        (ASSETS / f"{stem}.ru.svg").write_text(source, encoding="utf-8")
        english = source
        for russian, translated in translations.items():
            if russian not in english:
                raise RuntimeError(f"Missing SVG source text: {russian}")
            english = english.replace(russian, translated)
        (ASSETS / f"{stem}.en.svg").write_text(english, encoding="utf-8")


if __name__ == "__main__":
    build()
