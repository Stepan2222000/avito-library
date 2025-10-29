"""Debug utilities for capturing Playwright screenshots near call sites."""

from __future__ import annotations

import inspect
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from playwright.async_api import Error as PlaywrightError, Page, TimeoutError as PlaywrightTimeoutError

__all__ = ["capture_debug_screenshot", "DEBUG_SCREENSHOTS"]

logger = logging.getLogger(__name__)


def _read_positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(0, value)


DEBUG_SCREENSHOTS: int = _read_positive_int_env("AVITO_DEBUG_SCREENSHOTS", 0)
DEBUG_SCREENSHOT_TIMEOUT_MS: int = _read_positive_int_env("AVITO_DEBUG_SCREENSHOT_TIMEOUT_MS", 5_000)


def _resolve_caller_info() -> Tuple[Path, Optional[str]]:
    """Return the absolute path and function name of the caller."""
    current_file = Path(__file__).resolve()
    stack = inspect.stack()
    try:
        for frame_info in stack[1:]:
            caller_path = Path(frame_info.filename).resolve()
            # Skip frames that originate from this debug module itself.
            if caller_path == current_file:
                continue
            function_name = frame_info.function or None
            return caller_path, function_name
    finally:
        # Avoid reference cycles
        del stack
    raise RuntimeError("Unable to determine caller file for debug screenshot")


def _sanitize_label(label: str | None) -> str:
    if not label:
        return ""
    cleaned = []
    for char in label.lower():
        if char.isalnum() or char in {"-", "_"}:
            cleaned.append(char)
        else:
            cleaned.append("-")
    sanitized = "".join(cleaned).strip("-")
    return sanitized


def _sanitize_fragment(fragment: str | None) -> str | None:
    if not fragment:
        return None
    cleaned = []
    for char in fragment.lower():
        if char.isalnum() or char in {"-", "_"}:
            cleaned.append(char)
        else:
            cleaned.append("-")
    sanitized = "".join(cleaned).strip("-")
    return sanitized or None


def _build_target_path(base_dir: Path, label: str | None) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    label_part = _sanitize_label(label)
    if label_part:
        filename = f"{timestamp}_{label_part}.png"
    else:
        filename = f"{timestamp}.png"
    candidate = base_dir / filename
    counter = 1
    while candidate.exists():
        candidate = base_dir / f"{filename[:-4]}_{counter:02d}.png"
        counter += 1
    return candidate


async def capture_debug_screenshot(
    page: Page,
    *,
    enabled: Optional[int] = None,
    label: Optional[str] = None,
    subfolder: Optional[str] = None,
    full_page: bool = True,
    timeout_ms: Optional[int] = None,
) -> Optional[Path]:
    """Capture a screenshot next to the caller file when debug is enabled.

    Args:
        page: Playwright page instance to capture.
        enabled: Toggle flag (0 disables capturing, non-zero enables). If ``None``,
            uses :data:`DEBUG_SCREENSHOTS`.
        label: Optional text appended to the filename for context.
        subfolder: Optional override for folder name inside caller directory. If not
            provided, the caller function name is used when available.
        full_page: Whether to capture the full scrollable page.
        timeout_ms: Optional override for screenshot timeout in milliseconds. Defaults
            to :data:`DEBUG_SCREENSHOT_TIMEOUT_MS` when ``None``.

    Returns:
        Path to the saved screenshot or ``None`` when disabled or when capturing
        fails (in which case a ``.timeout.txt``/``.error.txt`` file with details is
        written beside the expected screenshot).
    """

    toggle = DEBUG_SCREENSHOTS if enabled is None else enabled

    if not toggle:
        return None

    caller_file, function_name = _resolve_caller_info()
    target_dir = caller_file.parent / caller_file.stem

    folder_fragment = _sanitize_fragment(subfolder)
    if folder_fragment is None:
        function_fragment = None if function_name in {None, "<module>"} else _sanitize_fragment(function_name)
        folder_fragment = function_fragment

    if folder_fragment:
        target_dir = target_dir / folder_fragment

    target_dir.mkdir(parents=True, exist_ok=True)

    screenshot_path = _build_target_path(target_dir, label)

    screenshot_timeout = DEBUG_SCREENSHOT_TIMEOUT_MS
    if timeout_ms is not None:
        try:
            screenshot_timeout = max(0, int(timeout_ms))
        except (TypeError, ValueError):
            screenshot_timeout = DEBUG_SCREENSHOT_TIMEOUT_MS

    try:
        await page.screenshot(
            path=str(screenshot_path),
            full_page=full_page,
            type="png",
            timeout=float(screenshot_timeout),
            animations="disabled",
        )
    except PlaywrightTimeoutError as exc:
        failure_path = screenshot_path.with_suffix(".timeout.txt")
        failure_path.write_text(
            f"Screenshot timeout after {screenshot_timeout} ms: {exc}\n",
            encoding="utf-8",
        )
        logger.debug("Debug screenshot timed out after %sms: %s", screenshot_timeout, exc)
        return None
    except PlaywrightError as exc:
        failure_path = screenshot_path.with_suffix(".error.txt")
        failure_path.write_text(
            f"Failed to capture screenshot: {exc}\n",
            encoding="utf-8",
        )
        logger.debug("Debug screenshot failed: %s", exc)
        return None

    return screenshot_path
