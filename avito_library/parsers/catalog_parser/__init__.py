"""Экспорты пакета парсера каталога.

Новый API v2:
- navigate_to_catalog() — переход на страницу каталога
- parse_single_page() — парсинг одной страницы
- parse_catalog() — парсинг всех страниц с пагинацией

Legacy API:
- parse_catalog_legacy() — старая версия parse_catalog (для обратной совместимости)
"""

# Legacy (старый API)
from .catalog_parser import parse_catalog as parse_catalog_legacy

# v2 API
from .catalog_parser_v2 import parse_catalog, parse_single_page
from .navigation import navigate_to_catalog
from .models import (
    CatalogListing,
    CatalogParseMeta,
    CatalogParseResult,
    CatalogParseResultLegacy,
    CatalogParseStatus,
    SinglePageResult,
)

__all__ = [
    # Модели
    "CatalogListing",
    "CatalogParseMeta",
    "CatalogParseResult",
    "CatalogParseResultLegacy",
    "CatalogParseStatus",
    "SinglePageResult",
    # v2 функции
    "navigate_to_catalog",
    "parse_single_page",
    "parse_catalog",
    # Legacy (deprecated)
    "parse_catalog_legacy",
]
