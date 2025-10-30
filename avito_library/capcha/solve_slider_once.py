"""Single attempt solver for Geetest slider captcha."""

from __future__ import annotations

import time
import re
from typing import Any

from playwright.async_api import Page, TimeoutError

from .solver_utils import calculate_hash, calculate_offset
from .cache_manager import (
    FAILURE_THRESHOLD,
    get_offset,
    record_failure,
    update_offset,
)
__all__ = ["solve_slider_once"]


async def solve_slider_once(page: Page) -> tuple[str, bool]:
    """Perform one attempt to solve Geetest slider captcha."""

    try:
        pi_elem = await page.locator("div.geetest_slice_bg").first.get_attribute("style")
        back_elem = await page.locator("div.geetest_bg").first.get_attribute("style")
        pi_style = await page.locator("div.geetest_slice").first.get_attribute("style")
    except TimeoutError as exc:
        raise RuntimeError("style-timeout") from exc
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"style-error:{exc}") from exc

    if not (pi_elem and back_elem and pi_style):
        raise RuntimeError("style-empty")

    try:
        pi_url = re.findall(r'url\("(.*?)"\)', pi_elem)[0]
        back_url = re.findall(r'url\("(.*?)"\)', back_elem)[0]
        pi_top = float(re.findall(r"top: (.*?)px;", pi_style)[0])
    except (IndexError, ValueError) as exc:
        print("5")
        raise RuntimeError(f"style-parse:{exc}") from exc

    try:
        back_content = await page.request.get(back_url, fail_on_status_code=True)
        pi_content = await page.request.get(pi_url, fail_on_status_code=True)
        back_body = await back_content.body()
        pi_body = await pi_content.body()
    except Exception as exc:  # noqa: BLE001
        print("6")
        raise RuntimeError(f"image-fetch:{exc}") from exc

    h_content = calculate_hash(back_body, pi_body)
    cache_entry: dict[str, Any] | None = await get_offset(h_content)
    used_cached_offset = cache_entry is not None
    print(cache_entry)
    if cache_entry is None:
        base_offset = calculate_offset(back_body, pi_body, pi_top)
        definitely_known = False
    else:
        base_offset = int(cache_entry.get("offset", 0))
        definitely_known = bool(cache_entry.get("definitely"))

    try:
        await page.wait_for_selector(".geetest_track")
        geetest_btn = page.locator(".geetest_track > .geetest_btn").last
        await geetest_btn.hover()
        await page.mouse.down()
        geetest_btn_box = await geetest_btn.bounding_box()
    except TimeoutError as exc:
        raise RuntimeError("track-timeout") from exc
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"track-error:{exc}") from exc

    if geetest_btn_box is None:
        raise RuntimeError("bbox-none")

    move_offset = base_offset + 37
    await page.mouse.move(
        geetest_btn_box["x"] + move_offset,
        geetest_btn_box["y"],
    )
    await page.mouse.up()

    deadline = time.monotonic() + 5.0
    solved = False
    page_html = ""

    while time.monotonic() < deadline:
        try:
            box = await page.query_selector("div.geetest_box")
            bg = await page.query_selector("div.geetest_slice_bg")
            slider = await page.query_selector("div.geetest_slice")
        except Exception:  # noqa: BLE001
            page_html = await page.content()
            solved = True
            break
        if not (box or bg or slider):
            page_html = await page.content()
            solved = True
            break

    if solved:
        await update_offset(
            h_content,
            offset=base_offset,
            definitely=True,
            fail_count=0,
        )
        return page_html, True

    failure_html = await page.content()
    if used_cached_offset:
        removed = await record_failure(h_content)
        if removed:
            print(
                f"[captcha-cache] removed cached offset {h_content} after {FAILURE_THRESHOLD} consecutive failures",
            )
    return failure_html, False
