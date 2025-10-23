"""Пакет парсера каталога Авито."""

from .catalog_parser import parse_catalog
from .models import (
    CatalogListing,
    CatalogParseMeta,
    CatalogParseResult,
    CatalogParseStatus,
)
from .stream import (
    PageRequest,
    parse_catalog_until_complete,
    set_page_exchange,
    supply_page,
    wait_for_page_request,
)

__all__ = [
    "parse_catalog",
    "CatalogListing",
    "CatalogParseMeta",
    "CatalogParseResult",
    "CatalogParseStatus",
    "parse_catalog_until_complete",
    "PageRequest",
    "wait_for_page_request",
    "supply_page",
    "set_page_exchange",
]
