"""Card parser draft implementation for Avito library plan."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional
from bs4 import BeautifulSoup

__all__ = ["CardData", "CardParsingError", "parse_card"]


class CardParsingError(RuntimeError):
    """Raised when HTML does not correspond to an Avito card."""


@dataclass(slots=True)
class CardData:
    title: Optional[str] = None
    price: Optional[int] = None
    seller: Optional[dict[str, Optional[str]]] = None
    item_id: Optional[int] = None
    published_at: Optional[str] = None
    description: Optional[str] = None
    location: Optional[dict[str, Optional[str]]] = None
    characteristics: Optional[dict[str, str]] = None
    views_total: Optional[int] = None
    raw_html: Optional[str] = None


_SUPPORTED_FIELDS = {
    "title",
    "price",
    "seller",
    "item_id",
    "published_at",
    "description",
    "location",
    "characteristics",
    "views_total",
    "raw_html",
}


def parse_card(
    html: str,
    *,
    fields: Iterable[str],
    ensure_card: bool = True,
    include_html: bool = False,
) -> CardData:
    """Parses Avito card HTML and returns populated CardData."""


    if not isinstance(html, str) or not html.strip():
        raise ValueError("html must be a non-empty string")

    requested_fields = {field for field in fields if field in _SUPPORTED_FIELDS}
    soup = BeautifulSoup(html, "lxml")

    if ensure_card and not _is_card_html(soup):
        raise CardParsingError("HTML is not recognized as an Avito card")

    data = CardData()

    if "title" in requested_fields:
        data.title = _extract_text(
            soup.select_one('h1[itemprop="name"]')
            or soup.select_one('h1[data-marker="item-view/title-info"]')
        )

    if "price" in requested_fields:
        data.price = _extract_price(soup)

    if "seller" in requested_fields:
        data.seller = _extract_seller(soup)

    if "item_id" in requested_fields or "published_at" in requested_fields:
        item_id, published_at = _extract_item_meta(soup)
        if "item_id" in requested_fields:
            data.item_id = item_id
        if "published_at" in requested_fields:
            data.published_at = published_at

    if "description" in requested_fields:
        data.description = _extract_description(soup)

    if "location" in requested_fields:
        data.location = _extract_location(soup)

    if "characteristics" in requested_fields:
        data.characteristics = _extract_characteristics(soup)

    if "views_total" in requested_fields:
        data.views_total = _extract_views(soup)

    if include_html:
        data.raw_html = html
    elif "raw_html" in requested_fields:
        data.raw_html = html

    return data


def _is_card_html(soup: BeautifulSoup) -> bool:
    """Uses card_found_detector logic to ensure card markup is present."""

    locator = soup.select_one('span[data-marker="item-view/item-id"]')
    return locator is not None


def _extract_text(node) -> Optional[str]:
    if node is None:
        return None
    text = node.get_text(strip=True)
    return text or None


def _extract_price(soup: BeautifulSoup) -> Optional[int]:
    node = (
        soup.select_one('span[itemprop="price"][data-marker="item-view/item-price"]')
        or soup.select_one('meta[itemprop="price"]')
    )
    if node is None:
        return None
    value = node.get("content") or node.get("value") or node.get_text(strip=True)
    if not value:
        return None
    digits = re.sub(r"[^0-9]", "", value)
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _extract_seller(soup: BeautifulSoup) -> Optional[dict[str, Optional[str]]]:
    name_node = soup.select_one('div[data-marker="seller-info/name"] span')
    link_node = (
        soup.select_one('a[data-marker="seller-link/link"]')
        or soup.select_one('a[data-marker="seller-info/profile-link"]')
    )
    name = _extract_text(name_node)
    profile_url = link_node.get("href") if link_node else None
    if name is None and profile_url is None:
        return None
    return {"name": name, "profile_url": profile_url}


def _extract_item_meta(soup: BeautifulSoup) -> tuple[Optional[int], Optional[str]]:
    id_node = soup.select_one('span[data-marker="item-view/item-id"]')
    date_node = soup.select_one('span[data-marker="item-view/item-date"]')

    item_id: Optional[int] = None
    if id_node is not None:
        digits = re.findall(r"\d+", id_node.get_text(separator=" ", strip=True))
        item_id = int(digits[0]) if digits else None

    published_at = _extract_text(date_node)
    if published_at is None and id_node is not None:
        raw = id_node.get_text(separator=" ", strip=True)
        if "·" in raw:
            parts = [part.strip() for part in raw.split("·", 1)]
            if len(parts) == 2:
                published_at = parts[1] or None
    if published_at and published_at.startswith("·"):
        published_at = published_at.lstrip("· ")

    return item_id, published_at


def _extract_description(soup: BeautifulSoup) -> Optional[str]:
    node = soup.select_one('div[data-marker="item-view/item-description"]') or soup.select_one(
        '#bx_item-description'
    )
    if node is None:
        return None
    text = node.get_text("\n", strip=True)
    return text or None


def _extract_location(soup: BeautifulSoup) -> Optional[dict[str, Optional[str]]]:
    container = soup.select_one('div[itemtype="http://schema.org/PostalAddress"]') or soup.select_one(
        'div[data-marker="item-view/item-location"]'
    )
    if container is None:
        return None

    address = None
    metro = None
    region = None

    for span in container.select("span"):
        text = span.get_text(" ", strip=True)
        if not text:
            continue
        marker = (span.get("data-marker") or "").lower()
        itemprop = (span.get("itemprop") or "").lower()
        if address is None and ("address" in marker or itemprop in {"address", "streetaddress", "addresslocality"}):
            address = text
        elif metro is None and ("metro" in marker or itemprop == "metrostation"):
            metro = text
        elif region is None and any(key in marker for key in ("region", "district", "area")):
            region = text

    if address is None:
        address = container.get_text(" ", strip=True) or None

    if address is None and metro is None and region is None:
        return None

    return {"address": address, "metro": metro, "region": region}


def _extract_characteristics(soup: BeautifulSoup) -> Optional[dict[str, str]]:
    items = soup.select('#bx_item-params li')
    if not items:
        items = soup.select('li[data-marker="item-parameters/list-item"]')
    characteristics: dict[str, str] = {}
    for item in items:
        parts = list(item.stripped_strings)
        if not parts:
            continue
        key = parts[0]
        value = " ".join(parts[1:]) if len(parts) > 1 else ""
        if value.startswith(":"):
            value = value.lstrip(": 	")
        if key:
            characteristics[key] = value
    return characteristics or None


def _extract_views(soup: BeautifulSoup) -> Optional[int]:
    node = soup.select_one('span[data-marker="item-view/total-views"]')
    if node is None:
        return None
    digits = re.findall(r"\d+", node.get_text())
    if not digits:
        return None
    try:
        return int("".join(digits))
    except ValueError:
        return None
