"""Экспорты пакета парсера каталога.

API v2:
- navigate_to_catalog() — переход на страницу каталога
- parse_single_page() — парсинг одной страницы
- parse_catalog() — парсинг всех страниц с пагинацией и фильтрами
"""

from .catalog_parser_v2 import parse_catalog, parse_single_page
from .navigation import navigate_to_catalog
from .models import (
    CatalogListing,
    CatalogParseMeta,
    CatalogParseResult,
    CatalogParseStatus,
    SinglePageResult,
)

__all__ = [
    # Модели
    "CatalogListing",
    "CatalogParseMeta",
    "CatalogParseResult",
    "CatalogParseStatus",
    "SinglePageResult",
    # Функции
    "navigate_to_catalog",
    "parse_single_page",
    "parse_catalog",
]
