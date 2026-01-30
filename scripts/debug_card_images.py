import argparse
import asyncio
import json
import os
import re
import time
from dataclasses import asdict
from typing import Iterable, List, Optional, Tuple

from playwright.async_api import async_playwright

from avito_library import parse_card, CardParseStatus

DEBUG_LOG_PATH = "/Users/stepanorlov/Desktop/DONE/avito-library/.cursor/debug.log"

DEFAULT_NO_PHOTO_IDS = [
    2943307363,
    4841706905,
    4480047858,
    7502179345,
    4841954252,
]

DEFAULT_WITH_PHOTO_IDS = [
    7531386217,
    2602948816,
    2971925787,
    2492276057,
    7257544197,
]


def _debug_log(payload: dict) -> None:
    try:
        with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True) + "\n")
    except Exception:
        pass


def _build_urls(item_ids: Iterable[int]) -> List[str]:
    return [f"https://www.avito.ru/items/{item_id}" for item_id in item_ids]


def _extract_item_id(url: str) -> Optional[int]:
    match = re.search(r"/items/(\d+)", url)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


async def _parse_one(
    page,
    url: str,
    *,
    fields: Iterable[str],
    run_index: int,
) -> Tuple[str, Optional[dict]]:
    response = await page.goto(url, wait_until="domcontentloaded")
    # region agent log
    _debug_log(
        {
            "sessionId": "debug-session",
            "runId": "pre-fix",
            "hypothesisId": "H1",
            "location": "debug_card_images.py:_parse_one",
            "message": "card_parse_start",
            "data": {
                "url": url,
                "status": response.status if response else None,
                "run_index": run_index,
            },
            "timestamp": int(time.time() * 1000),
        }
    )
    # endregion agent log
    result = await parse_card(page, last_response=response, fields=fields, include_html=False)
    if result.status != CardParseStatus.SUCCESS or result.data is None:
        return url, {
            "status": result.status.value,
            "images_count": None,
            "images_urls_count": None,
            "images_errors_count": None,
            "run_index": run_index,
        }

    data = result.data
    return url, {
        "status": result.status.value,
        "images_count": len(data.images or []),
        "images_urls_count": len(data.images_urls or []),
        "images_errors_count": len(data.images_errors or []),
        "run_index": run_index,
    }


async def run_test(
    urls: List[str],
    *,
    headless: bool,
    repeat: int,
) -> List[Tuple[str, Optional[dict]]]:
    results: List[Tuple[str, Optional[dict]]] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context()
        page = await context.new_page()

        for run_index in range(repeat):
            for url in urls:
                try:
                    result = await _parse_one(
                        page,
                        url,
                        fields=[
                            "item_id",
                            "title",
                            "images",
                        ],
                        run_index=run_index,
                    )
                    results.append(result)
                except Exception as exc:
                    results.append(
                        (url, {"status": "exception", "error": str(exc)[:200], "run_index": run_index})
                    )

        await context.close()
        await browser.close()

    return results


def _write_json(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--category",
        choices=("all", "no-photos", "with-photos"),
        default="all",
    )
    parser.add_argument("--urls", nargs="*", default=None)
    parser.add_argument("--ids", nargs="*", type=int, default=None)
    parser.add_argument("--headless", action="store_true", default=False)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--output", default="image_parse_results.json")
    args = parser.parse_args()

    urls: List[str] = []
    if args.urls:
        urls.extend(args.urls)
    if args.ids:
        urls.extend(_build_urls(args.ids))
    if not urls:
        if args.category == "no-photos":
            ids = DEFAULT_NO_PHOTO_IDS
        elif args.category == "with-photos":
            ids = DEFAULT_WITH_PHOTO_IDS
        else:
            ids = DEFAULT_NO_PHOTO_IDS + DEFAULT_WITH_PHOTO_IDS
        urls = _build_urls(ids)
    headless = args.headless or os.environ.get("HEADLESS", "0") == "1"

    results = asyncio.run(run_test(urls, headless=headless, repeat=max(1, args.repeat)))

    summary = {
        "total": len(results),
        "missing_images": [],
        "errors": [],
        "results": [],
    }

    for url, data in results:
        item_id = _extract_item_id(url)
        summary["results"].append({"url": url, "data": data})
        if not data:
            summary["errors"].append({"url": url, "item_id": item_id, "reason": "no_data"})
            continue
        if data.get("status") != "success":
            summary["errors"].append({"url": url, "item_id": item_id, "reason": data.get("status")})
            continue
        if data.get("images_count") == 0:
            summary["missing_images"].append(
                {"url": url, "item_id": item_id, "run_index": data.get("run_index")}
            )

    _write_json(args.output, summary)
    print(f"Saved report to {args.output}")
    if summary["missing_images"]:
        print("Missing images:")
        for item in summary["missing_images"]:
            print(f"- run={item['run_index']} {item['item_id']}: {item['url']}")
    if summary["errors"]:
        print("Errors:")
        for item in summary["errors"]:
            print(f"- {item['url']}: {item['reason']}")


if __name__ == "__main__":
    main()
