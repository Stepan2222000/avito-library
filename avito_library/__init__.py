"""Высокоуровневый API для `avito-library`.

Пакет собирает воедино детекторы состояний страниц, решатели капчи и парсеры
каталога/карточек/продавцов Авито. Все компоненты спроектированы для работы с
долговечной страницей Playwright и готовы к переиспользованию в асинхронных
пайплайнах без дополнительных HTTP-клиентов.
"""

from .capcha import resolve_captcha_flow, solve_slider_once
from .config import MAX_PAGE
from .detectors import (
    CAPTCHA_DETECTOR_ID,
    CARD_FOUND_DETECTOR_ID,
    CATALOG_DETECTOR_ID,
    CONTINUE_BUTTON_DETECTOR_ID,
    DETECTOR_DEFAULT_ORDER,
    DETECTOR_FUNCTIONS,
    DETECTOR_WAIT_TIMEOUT_RESOLVERS,
    DetectionError,
    PROXY_AUTH_DETECTOR_ID,
    PROXY_BLOCK_403_DETECTOR_ID,
    PROXY_BLOCK_429_DETECTOR_ID,
    REMOVED_DETECTOR_ID,
    SELLER_PROFILE_DETECTOR_ID,
    NOT_DETECTED_STATE_ID,
    detect_page_state,
)
from .install_browser import install_playwright_chromium
from .parsers import CardData, CardParsingError, parse_card
from .parsers.catalog_parser import (
    CatalogListing,
    CatalogParseMeta,
    CatalogParseResult,
    CatalogParseStatus,
    parse_catalog,
)
from .parsers.seller_profile_parser import (
    SellerIdNotFound,
    SellerProfileParsingResult,
    collect_seller_items,
)
from .utils import press_continue_and_detect

__all__ = [
    "MAX_PAGE",
    "detect_page_state",
    "DetectionError",
    "DETECTOR_FUNCTIONS",
    "DETECTOR_DEFAULT_ORDER",
    "DETECTOR_WAIT_TIMEOUT_RESOLVERS",
    "CAPTCHA_DETECTOR_ID",
    "NOT_DETECTED_STATE_ID",
    "CARD_FOUND_DETECTOR_ID",
    "CATALOG_DETECTOR_ID",
    "CONTINUE_BUTTON_DETECTOR_ID",
    "PROXY_AUTH_DETECTOR_ID",
    "PROXY_BLOCK_403_DETECTOR_ID",
    "PROXY_BLOCK_429_DETECTOR_ID",
    "REMOVED_DETECTOR_ID",
    "SELLER_PROFILE_DETECTOR_ID",
    "press_continue_and_detect",
    "resolve_captcha_flow",
    "solve_slider_once",
    "install_playwright_chromium",
    "CardData",
    "CardParsingError",
    "parse_card",
    "CatalogListing",
    "CatalogParseMeta",
    "CatalogParseResult",
    "CatalogParseStatus",
    "parse_catalog",
    "collect_seller_items",
    "SellerProfileParsingResult",
    "SellerIdNotFound",
]
