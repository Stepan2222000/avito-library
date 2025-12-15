"""Парсер каталога Avito v2 с поддержкой продолжения.

Новая архитектура с тремя уровнями API:
- navigate_to_catalog() — переход на страницу каталога
- parse_single_page() — парсинг одной страницы
- parse_catalog() — парсинг всех страниц с пагинацией

Ключевые отличия от v1:
- Страница уже открыта перед вызовом парсера
- Метод continue_from() для продолжения после ошибок прокси
- Нет глобального состояния — всё в объекте результата
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from avito_library.capcha import resolve_captcha_flow
from avito_library.detectors import (
    CAPTCHA_DETECTOR_ID,
    CARD_FOUND_DETECTOR_ID,
    CATALOG_DETECTOR_ID,
    CONTINUE_BUTTON_DETECTOR_ID,
    NOT_DETECTED_STATE_ID,
    PROXY_AUTH_DETECTOR_ID,
    PROXY_BLOCK_403_DETECTOR_ID,
    PROXY_BLOCK_429_DETECTOR_ID,
    REMOVED_DETECTOR_ID,
    SELLER_PROFILE_DETECTOR_ID,
    detect_page_state,
)

from .helpers import extract_listing, get_next_page_url, load_catalog_cards
from .models import (
    CatalogListing,
    CatalogParseMeta,
    CatalogParseResult,
    CatalogParseStatus,
    SinglePageResult,
)
from .navigation import navigate_to_catalog

if TYPE_CHECKING:
    pass

__all__ = ["parse_single_page", "parse_catalog"]


# === Классификация состояний страницы ===

# Состояния, при которых решаем капчу внутри парсера
_CAPTCHA_STATES = frozenset({
    CAPTCHA_DETECTOR_ID,
    PROXY_BLOCK_429_DETECTOR_ID,
    CONTINUE_BUTTON_DETECTOR_ID,
})

# Критические ошибки — возвращаем управление внешнему коду
_CRITICAL_STATES: dict[str, CatalogParseStatus] = {
    PROXY_BLOCK_403_DETECTOR_ID: CatalogParseStatus.PROXY_BLOCKED,
    PROXY_AUTH_DETECTOR_ID: CatalogParseStatus.PROXY_AUTH_REQUIRED,
    NOT_DETECTED_STATE_ID: CatalogParseStatus.PAGE_NOT_DETECTED,
}

# Неправильная страница — ошибка в логике вызывающего кода
_WRONG_PAGE_STATES = frozenset({
    CARD_FOUND_DETECTOR_ID,
    SELLER_PROFILE_DETECTOR_ID,
    REMOVED_DETECTOR_ID,
})


# === Публичные функции ===


async def parse_single_page(
    page: Page,
    *,
    fields: Iterable[str],
    include_html: bool = False,
    max_captcha_attempts: int = 30,
) -> SinglePageResult:
    """Парсит одну страницу каталога (страница уже открыта).

    Это низкоуровневая функция для парсинга одной страницы. Для парсинга
    нескольких страниц с пагинацией используйте parse_catalog().

    Что делает:
    1. Определяет состояние страницы через detect_page_state()
    2. Если капча/429/кнопка — решает внутри через resolve_captcha_flow()
    3. Если критическая ошибка — возвращает соответствующий статус
    4. Если каталог — парсит карточки и определяет следующую страницу

    Args:
        page: Playwright Page, уже открытая на каталоге.
        fields: Набор полей для извлечения из карточек
            (item_id, title, price, snippet, location, и т.д.).
        include_html: Сохранять ли HTML карточек в raw_html.
        max_captcha_attempts: Максимум попыток решения капчи.

    Returns:
        SinglePageResult с карточками или информацией об ошибке.

    Examples:
        >>> result = await parse_single_page(
        ...     page,
        ...     fields=["item_id", "title", "price"],
        ... )
        >>> if result.status == CatalogParseStatus.SUCCESS:
        ...     for card in result.cards:
        ...         print(card.title, card.price)
    """
    fields_set = set(fields)
    current_url = page.url

    # 1. Определяем состояние страницы
    state = await detect_page_state(page)

    # 2. Если капча/429/кнопка — решаем в цикле
    captcha_attempts = 0
    while state in _CAPTCHA_STATES and captcha_attempts < max_captcha_attempts:
        captcha_attempts += 1
        _, solved = await resolve_captcha_flow(page, max_attempts=1)

        if solved:
            state = await detect_page_state(page)
            if state == CATALOG_DETECTOR_ID:
                break
            # Проверяем на критические ошибки после решения капчи
            if state in _CRITICAL_STATES:
                return SinglePageResult(
                    status=_CRITICAL_STATES[state],
                    cards=[],
                    has_next=False,
                    error_state=state,
                    error_url=current_url,
                )
        # Капча не решена — следующая попытка

    # 3. Проверяем результат после попыток решения капчи
    if state in _CAPTCHA_STATES:
        return SinglePageResult(
            status=CatalogParseStatus.CAPTCHA_FAILED,
            cards=[],
            has_next=False,
            error_state=state,
            error_url=current_url,
        )

    # 4. Критические ошибки — возвращаем статус
    if state in _CRITICAL_STATES:
        return SinglePageResult(
            status=_CRITICAL_STATES[state],
            cards=[],
            has_next=False,
            error_state=state,
            error_url=current_url,
        )

    # 5. Неправильная страница — ошибка в логике вызывающего кода
    if state in _WRONG_PAGE_STATES:
        return SinglePageResult(
            status=CatalogParseStatus.WRONG_PAGE,
            cards=[],
            has_next=False,
            error_state=state,
            error_url=current_url,
        )

    # 6. Успех — парсим каталог
    if state == CATALOG_DETECTOR_ID:
        card_locators = await load_catalog_cards(page)
        cards: list[CatalogListing] = []

        for locator in card_locators:
            listing = await extract_listing(
                locator,
                fields_set,
                include_html=include_html,
            )
            if listing.item_id:
                cards.append(listing)

        has_next, next_url = await get_next_page_url(page, current_url)

        return SinglePageResult(
            status=CatalogParseStatus.SUCCESS,
            cards=cards,
            has_next=has_next,
            next_url=next_url,
        )

    # 7. Неизвестное состояние
    return SinglePageResult(
        status=CatalogParseStatus.PAGE_NOT_DETECTED,
        cards=[],
        has_next=False,
        error_state=state,
        error_url=current_url,
    )


async def parse_catalog(
    page: Page,
    catalog_url: str,
    *,
    fields: Iterable[str],
    max_pages: int | None = None,
    sort: str | None = None,
    start_page: int = 1,
    include_html: bool = False,
    max_captcha_attempts: int = 30,
    load_timeout: int = 180_000,
    load_retries: int = 5,
) -> CatalogParseResult:
    """Парсит все страницы каталога с пагинацией (страница уже открыта).

    Это главная функция для парсинга каталога. При критической ошибке
    возвращает результат с возможностью продолжения через continue_from().

    Что делает:
    1. Вызывает parse_single_page() для текущей страницы
    2. Накапливает карточки со всех страниц
    3. Переходит на следующую страницу через navigate_to_catalog()
    4. При ошибке возвращает результат с информацией для продолжения

    Args:
        page: Playwright Page, уже открытая на каталоге.
        catalog_url: Базовый URL каталога (для navigate_to_catalog при переходе
            на следующие страницы).
        fields: Набор полей для извлечения из карточек.
        max_pages: Максимум страниц для обработки. None = без лимита.
        sort: Сортировка: "date", "price_asc", "price_desc", "mileage_asc".
        start_page: С какой страницы начинать (для расчёта resume_page_number).
        include_html: Сохранять ли HTML карточек.
        max_captcha_attempts: Максимум попыток решения капчи на странице.
        load_timeout: Таймаут загрузки страницы в миллисекундах.
        load_retries: Количество retry при таймауте загрузки.

    Returns:
        CatalogParseResult с карточками и метаинформацией.
        При критической ошибке результат содержит resume_url и метод continue_from().

    Examples:
        >>> # Обычное использование
        >>> await navigate_to_catalog(page, "https://avito.ru/moskva/telefony")
        >>> result = await parse_catalog(
        ...     page,
        ...     "https://avito.ru/moskva/telefony",
        ...     fields=["title", "price"],
        ...     max_pages=10,
        ... )
        >>> print(f"Собрано {len(result.listings)} карточек")

        >>> # Продолжение после ошибки
        >>> if result.status == CatalogParseStatus.PROXY_BLOCKED:
        ...     new_page = await create_page_with_new_proxy()
        ...     result = await result.continue_from(new_page)
    """
    fields_set = set(fields)
    listings: list[CatalogListing] = []
    processed_pages = 0

    while True:
        # Проверяем лимит страниц
        if max_pages is not None and processed_pages >= max_pages:
            break

        # Парсим текущую страницу
        result = await parse_single_page(
            page,
            fields=fields_set,
            include_html=include_html,
            max_captcha_attempts=max_captcha_attempts,
        )

        # Ошибка — возвращаем с возможностью продолжения
        if result.status != CatalogParseStatus.SUCCESS:
            return _build_result(
                status=result.status,
                listings=listings,
                processed_pages=processed_pages,
                error_state=result.error_state,
                error_url=result.error_url,
                resume_url=page.url,
                resume_page_number=start_page + processed_pages,
                # Сохраняем параметры для continue_from
                catalog_url=catalog_url,
                fields=fields_set,
                max_pages=max_pages,
                sort=sort,
                start_page=start_page,
                include_html=include_html,
                max_captcha_attempts=max_captcha_attempts,
                load_timeout=load_timeout,
                load_retries=load_retries,
            )

        # Успех — накапливаем карточки
        listings.extend(result.cards)
        processed_pages += 1

        # Нет следующей страницы
        if not result.has_next:
            break

        # Достигнут лимит страниц
        if max_pages is not None and processed_pages >= max_pages:
            break

        # Переходим на следующую страницу с retry при таймауте
        next_url = result.next_url
        load_success = False

        for retry in range(load_retries):
            try:
                await navigate_to_catalog(
                    page,
                    next_url,
                    timeout=load_timeout,
                )
                load_success = True
                break
            except PlaywrightTimeout:
                if retry == load_retries - 1:
                    # Все retry исчерпаны
                    return _build_result(
                        status=CatalogParseStatus.LOAD_TIMEOUT,
                        listings=listings,
                        processed_pages=processed_pages,
                        error_state="timeout",
                        error_url=next_url,
                        resume_url=next_url,
                        resume_page_number=start_page + processed_pages,
                        catalog_url=catalog_url,
                        fields=fields_set,
                        max_pages=max_pages,
                        sort=sort,
                        start_page=start_page,
                        include_html=include_html,
                        max_captcha_attempts=max_captcha_attempts,
                        load_timeout=load_timeout,
                        load_retries=load_retries,
                    )
                # Пробуем ещё раз

        if not load_success:
            # Не должно происходить, но на всякий случай
            break

    # Успешное завершение
    return _build_result(
        status=CatalogParseStatus.SUCCESS,
        listings=listings,
        processed_pages=processed_pages,
        catalog_url=catalog_url,
        fields=fields_set,
        max_pages=max_pages,
        sort=sort,
        start_page=start_page,
        include_html=include_html,
        max_captcha_attempts=max_captcha_attempts,
        load_timeout=load_timeout,
        load_retries=load_retries,
    )


# === Внутренние функции ===


def _build_result(
    *,
    status: CatalogParseStatus,
    listings: list[CatalogListing],
    processed_pages: int,
    error_state: str | None = None,
    error_url: str | None = None,
    resume_url: str | None = None,
    resume_page_number: int | None = None,
    catalog_url: str = "",
    fields: set | None = None,
    max_pages: int | None = None,
    sort: str | None = None,
    start_page: int = 1,
    include_html: bool = False,
    max_captcha_attempts: int = 30,
    load_timeout: int = 180_000,
    load_retries: int = 5,
) -> CatalogParseResult:
    """Собирает CatalogParseResult со всеми полями."""
    meta = CatalogParseMeta(
        status=status,
        processed_pages=processed_pages,
        processed_cards=len(listings),
        last_state=error_state,
        last_url=error_url or resume_url,
    )

    return CatalogParseResult(
        status=status,
        listings=listings,
        meta=meta,
        error_state=error_state,
        error_url=error_url,
        resume_url=resume_url,
        resume_page_number=resume_page_number,
        _catalog_url=catalog_url,
        _fields=fields or set(),
        _max_pages=max_pages,
        _sort=sort,
        _start_page=start_page,
        _include_html=include_html,
        _max_captcha_attempts=max_captcha_attempts,
        _load_timeout=load_timeout,
        _load_retries=load_retries,
        _processed_pages=processed_pages,
    )


async def _continue_parsing(
    prev_result: CatalogParseResult,
    new_page: Page,
    skip_navigation: bool | None,
) -> CatalogParseResult:
    """Внутренняя функция для continue_from().

    Вызывается из CatalogParseResult.continue_from() через отложенный импорт.

    Args:
        prev_result: Предыдущий результат с ошибкой.
        new_page: Новая страница Playwright.
        skip_navigation: Управление навигацией.

    Returns:
        Новый CatalogParseResult с объединёнными данными.
    """
    # Определяем, нужен ли goto
    need_goto = False

    if skip_navigation is True:
        need_goto = False
    elif skip_navigation is False:
        need_goto = True
    else:
        # None — автоопределение через detect_page_state
        state = await detect_page_state(new_page)
        need_goto = state != CATALOG_DETECTOR_ID

    if need_goto and prev_result.resume_url:
        await navigate_to_catalog(
            new_page,
            prev_result.resume_url,
            timeout=prev_result._load_timeout,
        )

    # Вычисляем оставшиеся страницы
    remaining_pages = None
    if prev_result._max_pages is not None:
        remaining_pages = prev_result._max_pages - prev_result._processed_pages
        if remaining_pages <= 0:
            # Уже всё обработано
            return prev_result

    # Продолжаем парсинг
    continuation = await parse_catalog(
        new_page,
        prev_result._catalog_url,
        fields=prev_result._fields,
        max_pages=remaining_pages,
        sort=prev_result._sort,
        start_page=prev_result.resume_page_number or 1,
        include_html=prev_result._include_html,
        max_captcha_attempts=prev_result._max_captcha_attempts,
        load_timeout=prev_result._load_timeout,
        load_retries=prev_result._load_retries,
    )

    # Объединяем результаты
    merged_listings = prev_result.listings + continuation.listings
    merged_pages = prev_result._processed_pages + continuation._processed_pages

    return CatalogParseResult(
        status=continuation.status,
        listings=merged_listings,
        meta=CatalogParseMeta(
            status=continuation.status,
            processed_pages=merged_pages,
            processed_cards=len(merged_listings),
            last_state=continuation.error_state,
            last_url=continuation.error_url or continuation.resume_url,
        ),
        error_state=continuation.error_state,
        error_url=continuation.error_url,
        resume_url=continuation.resume_url,
        resume_page_number=continuation.resume_page_number,
        _catalog_url=prev_result._catalog_url,
        _fields=prev_result._fields,
        _max_pages=prev_result._max_pages,
        _sort=prev_result._sort,
        _start_page=prev_result._start_page,
        _include_html=prev_result._include_html,
        _max_captcha_attempts=prev_result._max_captcha_attempts,
        _load_timeout=prev_result._load_timeout,
        _load_retries=prev_result._load_retries,
        _processed_pages=merged_pages,
    )
