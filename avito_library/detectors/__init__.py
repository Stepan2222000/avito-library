"""Registry of detector implementations for page state detection."""

from __future__ import annotations

from typing import Callable

from .captcha_geetest_detector import (
    DETECTOR_ID as CAPTCHA_DETECTOR_ID,
    captcha_geetest_detector,
    resolve_wait_timeout as captcha_resolve_wait_timeout,
)
from .card_found_detector import (
    DETECTOR_ID as CARD_FOUND_DETECTOR_ID,
    card_found_detector,
)
from .catalog_page_detector import (
    DETECTOR_ID as CATALOG_DETECTOR_ID,
    catalog_page_detector,
)
from .continue_button_detector import (
    DETECTOR_ID as CONTINUE_BUTTON_DETECTOR_ID,
    continue_button_detector,
)
from .loading_detector import (
    DETECTOR_ID as LOADING_DETECTOR_ID,
    loading_detector,
)
from .proxy_auth_407_detector import (
    DETECTOR_ID as PROXY_AUTH_DETECTOR_ID,
    proxy_auth_407_detector,
)
from .proxy_block_403_detector import (
    DETECTOR_ID as PROXY_BLOCK_403_DETECTOR_ID,
    proxy_block_403_detector,
)
from .proxy_block_429_detector import (
    DETECTOR_ID as PROXY_BLOCK_429_DETECTOR_ID,
    proxy_block_429_detector,
)
from .removed_or_not_found_detector import (
    DETECTOR_ID as REMOVED_DETECTOR_ID,
    removed_or_not_found_detector,
)
from .seller_profile_detector import (
    DETECTOR_ID as SELLER_PROFILE_DETECTOR_ID,
    seller_profile_detector,
)

DetectorCallable = Callable[..., object]

DETECTOR_FUNCTIONS: dict[str, DetectorCallable] = {
    PROXY_BLOCK_403_DETECTOR_ID: proxy_block_403_detector,
    PROXY_BLOCK_429_DETECTOR_ID: proxy_block_429_detector,
    PROXY_AUTH_DETECTOR_ID: proxy_auth_407_detector,
    LOADING_DETECTOR_ID: loading_detector,
    REMOVED_DETECTOR_ID: removed_or_not_found_detector,
    SELLER_PROFILE_DETECTOR_ID: seller_profile_detector,
    CATALOG_DETECTOR_ID: catalog_page_detector,
    CARD_FOUND_DETECTOR_ID: card_found_detector,
    CONTINUE_BUTTON_DETECTOR_ID: continue_button_detector,
    CAPTCHA_DETECTOR_ID: captcha_geetest_detector,
}

DETECTOR_DEFAULT_ORDER: tuple[str, ...] = (
    PROXY_BLOCK_403_DETECTOR_ID,
    PROXY_BLOCK_429_DETECTOR_ID,
    PROXY_AUTH_DETECTOR_ID,
    LOADING_DETECTOR_ID,  # High priority: detect loading state early
    CAPTCHA_DETECTOR_ID,
    REMOVED_DETECTOR_ID,
    SELLER_PROFILE_DETECTOR_ID,
    CATALOG_DETECTOR_ID,
    CARD_FOUND_DETECTOR_ID,
    CONTINUE_BUTTON_DETECTOR_ID,
)

DETECTOR_WAIT_TIMEOUT_RESOLVERS: dict[str, Callable[..., float]] = {
    CAPTCHA_DETECTOR_ID: captcha_resolve_wait_timeout,
}

from .detect_page_state import detect_page_state, DetectionError, NOT_DETECTED_STATE_ID  # noqa: E402

__all__ = [
    "DETECTOR_FUNCTIONS",
    "DETECTOR_DEFAULT_ORDER",
    "DETECTOR_WAIT_TIMEOUT_RESOLVERS",
    "detect_page_state",
    "DetectionError",
    "NOT_DETECTED_STATE_ID",
    "CAPTCHA_DETECTOR_ID",
    "CARD_FOUND_DETECTOR_ID",
    "CATALOG_DETECTOR_ID",
    "CONTINUE_BUTTON_DETECTOR_ID",
    "LOADING_DETECTOR_ID",
    "PROXY_AUTH_DETECTOR_ID",
    "PROXY_BLOCK_403_DETECTOR_ID",
    "PROXY_BLOCK_429_DETECTOR_ID",
    "REMOVED_DETECTOR_ID",
    "SELLER_PROFILE_DETECTOR_ID",
]
