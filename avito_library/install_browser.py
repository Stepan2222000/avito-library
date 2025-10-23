"""Вспомогательная команда для установки Chromium через Playwright.

Модуль предоставляет функцию `install_playwright_chromium`, которую можно
вызвать из Python-кода или через CLI (точка входа `avito-install-chromium`).
Команда всего лишь проксирует `python -m playwright install chromium`, чтобы
в DevOps- или Docker-сценариях не приходилось запоминать оригинальный вызов.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Final

__all__ = ["install_playwright_chromium", "install_playwright_chromium_cli"]

_PLAYWRIGHT_INSTALL_ARGS: Final[list[str]] = [
    "-m",
    "playwright",
    "install",
    "chromium",
]


def install_playwright_chromium(*, check: bool = True) -> int:
    """Запускает установку Chromium-браузера через Playwright CLI."""

    result = subprocess.run(
        [sys.executable, *_PLAYWRIGHT_INSTALL_ARGS],
        check=check,
    )
    return result.returncode


def install_playwright_chromium_cli() -> None:
    """CLI-обёртка: завершает процесс тем же кодом выхода, что и Playwright."""

    raise SystemExit(install_playwright_chromium(check=False))
