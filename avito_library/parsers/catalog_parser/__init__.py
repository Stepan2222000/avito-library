"""Compatibility exports for catalog parser package."""

from .catalog_parser import parse_catalog
from .models import (
    CatalogListing,
    CatalogParseMeta,
    CatalogParseResult,
    CatalogParseStatus,
)
from .steam import (
    parse_catalog_until_complete,
    set_page_exchange,
    supply_page,
    wait_for_page_request,
)

__all__ = [
    "CatalogListing",
    "CatalogParseMeta",
    "CatalogParseResult",
    "CatalogParseStatus",
    "parse_catalog",
    "parse_catalog_until_complete",
    "set_page_exchange",
    "supply_page",
    "wait_for_page_request",
]
