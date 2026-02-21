"""Парсер каталога Avito v2 с поддержкой продолжения и фильтрации.

Новая архитектура с тремя уровнями API:
- navigate_to_catalog() — переход на страницу каталога
- parse_single_page() — парсинг одной страницы
- parse_catalog() — парсинг всех страниц с пагинацией и фильтрами

Поддержка фильтрации:
- URL-фильтры: город, категория, марка, модель, кузов, топливо, коробка
- GET-параметры: цена, радиус, сортировка
- Механические фильтры: состояние, год, пробег, объём, привод, мощность, турбина, продавцы
"""

from __future__ import annotations

import asyncio
import logging
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
    SERVER_ERROR_5XX_DETECTOR_ID,
    UNKNOWN_PAGE_DETECTOR_ID,
    detect_page_state,
)

from .helpers import extract_listing, get_next_page_url, load_catalog_cards
from .mechanical_filters import apply_mechanical_filters
from .models import (
    CatalogListing,
    CatalogParseMeta,
    CatalogParseResult,
    CatalogParseStatus,
    SinglePageResult,
)
from .navigation import navigate_to_catalog
from .url_builder import build_catalog_url, merge_url_with_params

logger = logging.getLogger(__name__)

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
    max_srcset_wait_ms: int = 3000,
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
        >>> if result.status in {CatalogParseStatus.SUCCESS, CatalogParseStatus.EMPTY}:
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
    # или unknown_page_detector (известный edge case типа "журнал")
    if state in _WRONG_PAGE_STATES or (
        isinstance(state, str) and state.startswith(UNKNOWN_PAGE_DETECTOR_ID)
    ):
        return SinglePageResult(
            status=CatalogParseStatus.WRONG_PAGE,
            cards=[],
            has_next=False,
            error_state=state,
            error_url=current_url,
        )

    # 6. Успех — парсим каталог
    if state == CATALOG_DETECTOR_ID:
        card_locators, prefetched_images = await load_catalog_cards(
            page,
            preload_images="images" in fields_set,
            max_srcset_wait_ms=max_srcset_wait_ms,
        )
        cards: list[CatalogListing] = []

        for locator in card_locators:
            listing = await extract_listing(
                locator,
                fields_set,
                include_html=include_html,
                prefetched_images=prefetched_images,
            )
            if listing.item_id:
                cards.append(listing)

        has_next, next_url = await get_next_page_url(page, current_url)

        status = (
            CatalogParseStatus.EMPTY
            if not card_locators
            else CatalogParseStatus.SUCCESS
        )

        return SinglePageResult(
            status=status,
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
    url: str | None = None,
    *,
    # Параметры для построения URL (ЧПУ-сегменты)
    city: str | None = None,
    category: str | None = None,
    brand: str | None = None,
    model: str | None = None,
    body_type: str | None = None,
    fuel_type: str | None = None,
    transmission: list[str] | None = None,
    # GET-параметры
    price_min: int | None = None,
    price_max: int | None = None,
    radius: int | None = None,
    sort: str | None = None,
    # Механические фильтры
    condition: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    mileage_from: int | None = None,
    mileage_to: int | None = None,
    engine_volumes: list[float] | None = None,
    drive: list[str] | None = None,
    power_from: int | None = None,
    power_to: int | None = None,
    turbo: bool | None = None,
    seller_type: str | None = None,
    # Параметры парсинга
    fields: Iterable[str],
    max_pages: int | None = None,
    start_page: int = 1,
    single_page: bool = False,
    include_html: bool = False,
    max_captcha_attempts: int = 30,
    load_timeout: int = 180_000,
    load_retries: int = 5,
    max_srcset_wait_ms: int = 3000,
    _skip_navigation: bool = False,
) -> CatalogParseResult:
    """Парсит каталог Avito с автоматическим применением фильтров.

    Главная функция для парсинга каталога. Автоматически:
    - Строит URL с ЧПУ-сегментами и GET-параметрами
    - Применяет механические фильтры через Playwright
    - Парсит все страницы с пагинацией

    Args:
        page: Playwright Page.
        url: Готовый URL каталога (опционально). Если передан — параметры
            фильтрации объединяются с параметрами из URL.

        # ЧПУ-сегменты (русский язык, нормализуется автоматически):
        city: Slug города ("moskva", "spb"). None = все регионы.
        category: Slug категории ("avtomobili"). Обязателен если url не передан!
        brand: Slug марки ("bmw", "toyota").
        model: Slug модели ("x5", "camry").
        body_type: Тип кузова ("Седан", "Внедорожник").
        fuel_type: Тип топлива ("Бензин", "Дизель").
        transmission: Коробка передач (["Механика"], ["Механика", "Автомат"]).
            Если одно значение — применяется через URL.
            Если несколько — применяется механически.

        # GET-параметры:
        price_min, price_max: Диапазон цены (рубли).
        radius: Радиус поиска (0, 50, 100, 200, 300, 500 км).
        sort: Сортировка ("date", "price_asc", "price_desc", "mileage_asc").

        # Механические фильтры (применяются через Playwright):
        condition: Состояние товара — точный текст кнопки на странице
            (например, "С пробегом", "Новые", "Б/у"). Зависит от категории.
        year_from, year_to: Год выпуска.
        mileage_from, mileage_to: Пробег (км).
        engine_volumes: Объёмы двигателя ([2.0, 2.5]).
        drive: Тип привода (["Полный"], ["Передний", "Задний"]).
        power_from, power_to: Мощность (л.с.).
        turbo: Наличие турбины (True/False).
        seller_type: Тип продавца ("Дилеры", "Частные").

        # Параметры парсинга:
        fields: Поля для извлечения ("item_id", "title", "price", ...).
        max_pages: Максимум страниц. None = без лимита.
        start_page: Начальная страница (для resume).
        include_html: Сохранять HTML карточек.
        max_captcha_attempts: Попыток решения капчи.
        load_timeout: Таймаут загрузки (мс).
        load_retries: Retry при таймауте.

    Returns:
        CatalogParseResult с карточками и метаинформацией.

    Raises:
        ValueError: При конфликте параметров или отсутствии category.

    Examples:
        >>> # С готовым URL
        >>> result = await parse_catalog(
        ...     page,
        ...     url="https://www.avito.ru/moskva/avtomobili/bmw",
        ...     body_type="Седан",
        ...     price_min=500000,
        ...     fields=["item_id", "title", "price"],
        ... )

        >>> # С параметрами
        >>> result = await parse_catalog(
        ...     page,
        ...     city="moskva",
        ...     category="avtomobili",
        ...     brand="bmw",
        ...     body_type="Седан",
        ...     year_from=2018,
        ...     drive=["Полный"],
        ...     fields=["item_id", "title", "price"],
        ...     max_pages=10,
        ... )
    """
    fields_set = set(fields)
    listings: list[CatalogListing] = []
    processed_pages = 0

    # === Валидация single_page режима ===
    if single_page:
        if max_pages is not None:
            raise ValueError("max_pages нельзя указывать при single_page=True")
        if start_page > 1:
            raise ValueError("start_page нельзя указывать при single_page=True")
        max_pages = 1
        logger.info("Режим single_page: парсинг одной страницы")

    # === Логика построения URL и применения фильтров ===

    # При _skip_navigation=True (continue_from):
    # - Навигация уже сделана в _continue_parsing()
    # - URL страницы уже содержит все фильтры в параметре f
    if _skip_navigation:
        catalog_url = page.url
        need_mechanical = False
        last_response = None  # Навигация уже была сделана ранее
        logger.info("Пропускаем навигацию (_skip_navigation=True), URL: %s", catalog_url)
    else:
        # Определяем transmission для URL (только если одно значение)
        transmission_for_url: str | None = None
        transmission_mechanical: list[str] | None = None

        if transmission:
            if len(transmission) == 1:
                transmission_for_url = transmission[0]
            else:
                transmission_mechanical = transmission

        # Строим или парсим URL
        if url is not None:
            # URL передан — объединяем с параметрами
            merged_params, catalog_url = merge_url_with_params(
                url,
                city=city,
                category=category,
                brand=brand,
                model=model,
                body_type=body_type,
                fuel_type=fuel_type,
                transmission=transmission_for_url,
                price_min=price_min,
                price_max=price_max,
                radius=radius,
                sort=sort,
            )
            logger.info("URL построен из переданного: %s", catalog_url)
        else:
            # URL не передан — строим с нуля
            if category is None:
                raise ValueError("Параметр category обязателен если url не передан")

            catalog_url = build_catalog_url(
                city=city,
                category=category,
                brand=brand,
                model=model,
                body_type=body_type,
                fuel_type=fuel_type,
                transmission=transmission_for_url,
                price_min=price_min,
                price_max=price_max,
                radius=radius,
                sort=sort,
            )
            logger.info("URL построен: %s", catalog_url)

        # Определяем нужны ли механические фильтры
        need_mechanical = any([
            condition is not None,
            year_from is not None,
            year_to is not None,
            mileage_from is not None,
            mileage_to is not None,
            engine_volumes,
            transmission_mechanical,
            drive,
            power_from is not None,
            power_to is not None,
            turbo is not None,
            seller_type,
        ])

        # Переходим на страницу каталога
        last_response = await navigate_to_catalog(
            page,
            catalog_url,
            sort=sort,
            start_page=start_page,
            timeout=load_timeout,
        )

    # Проверяем состояние и решаем капчу после навигации (ОДНА проверка)
    state = await detect_page_state(page, last_response=last_response)

    # Retry при серверных ошибках 5xx (502, 503, 504)
    if state == SERVER_ERROR_5XX_DETECTOR_ID:
        retry_delays = (2.0, 4.0, 8.0)
        for delay in retry_delays:
            await asyncio.sleep(delay)
            last_response = await page.reload()
            state = await detect_page_state(page, last_response=last_response)
            if state != SERVER_ERROR_5XX_DETECTOR_ID:
                break
        else:
            # Все попытки исчерпаны — сервер недоступен
            return _build_result(
                status=CatalogParseStatus.SERVER_UNAVAILABLE,
                listings=[],
                processed_pages=0,
                error_state=state,
                error_url=page.url,
                resume_url=page.url,
                catalog_url=catalog_url,
                fields=fields_set,
                max_pages=max_pages,
                sort=sort,
                start_page=start_page,
                include_html=include_html,
                max_captcha_attempts=max_captcha_attempts,
                load_timeout=load_timeout,
                load_retries=load_retries,
                single_page=single_page,
            )
    captcha_attempts = 0
    while state in _CAPTCHA_STATES and captcha_attempts < max_captcha_attempts:
        captcha_attempts += 1
        _, solved = await resolve_captcha_flow(page, max_attempts=1)
        if solved:
            state = await detect_page_state(page)
            if state == CATALOG_DETECTOR_ID:
                break
            if state in _CRITICAL_STATES:
                return _build_result(
                    status=_CRITICAL_STATES[state],
                    listings=[],
                    processed_pages=0,
                    error_state=state,
                    error_url=page.url,
                    resume_url=page.url,
                    catalog_url=catalog_url,
                    fields=fields_set,
                    max_pages=max_pages,
                    sort=sort,
                    start_page=start_page,
                    include_html=include_html,
                    max_captcha_attempts=max_captcha_attempts,
                    load_timeout=load_timeout,
                    load_retries=load_retries,
                    single_page=single_page,
                )

    if state in _CAPTCHA_STATES:
        return _build_result(
            status=CatalogParseStatus.CAPTCHA_FAILED,
            listings=[],
            processed_pages=0,
            error_state=state,
            error_url=page.url,
            resume_url=page.url,
            catalog_url=catalog_url,
            fields=fields_set,
            max_pages=max_pages,
            sort=sort,
            start_page=start_page,
            include_html=include_html,
            max_captcha_attempts=max_captcha_attempts,
            load_timeout=load_timeout,
            load_retries=load_retries,
            single_page=single_page,
        )

    if state in _CRITICAL_STATES:
        return _build_result(
            status=_CRITICAL_STATES[state],
            listings=[],
            processed_pages=0,
            error_state=state,
            error_url=page.url,
            resume_url=page.url,
            catalog_url=catalog_url,
            fields=fields_set,
            max_pages=max_pages,
            sort=sort,
            start_page=start_page,
            include_html=include_html,
            max_captcha_attempts=max_captcha_attempts,
            load_timeout=load_timeout,
            load_retries=load_retries,
            single_page=single_page,
        )

    # Применяем механические фильтры если нужно
    if need_mechanical:
        logger.info("Применяем механические фильтры...")
        catalog_url = await apply_mechanical_filters(
            page,
            condition=condition,
            year_from=year_from,
            year_to=year_to,
            mileage_from=mileage_from,
            mileage_to=mileage_to,
            engine_volumes=engine_volumes,
            transmission=transmission_mechanical,
            drive=drive,
            power_from=power_from,
            power_to=power_to,
            turbo=turbo,
            seller_type=seller_type,
        )
        logger.info("Механические фильтры применены, новый URL: %s", catalog_url)

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
        if result.status not in {
            CatalogParseStatus.SUCCESS,
            CatalogParseStatus.EMPTY,
        }:
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
                single_page=single_page,
            )

        # Успех — накапливаем карточки
        listings.extend(result.cards)
        processed_pages += 1

        if result.status == CatalogParseStatus.EMPTY:
            break

        # Нет следующей страницы
        if not result.has_next:
            break

        # Достигнут лимит страниц
        if max_pages is not None and processed_pages >= max_pages:
            break

        # Переходим на следующую страницу с retry при таймауте и 5xx
        next_url = result.next_url
        load_success = False
        nav_response = None

        for retry in range(load_retries):
            try:
                nav_response = await navigate_to_catalog(
                    page,
                    next_url,
                    timeout=load_timeout,
                )

                # Проверяем на 5xx ошибки
                if nav_response and 500 <= nav_response.status < 600:
                    # Retry с exponential backoff
                    retry_delays = (2.0, 4.0, 8.0)
                    server_ok = False
                    for delay in retry_delays:
                        await asyncio.sleep(delay)
                        nav_response = await page.reload()
                        if nav_response and not (500 <= nav_response.status < 600):
                            server_ok = True
                            break
                    if not server_ok:
                        return _build_result(
                            status=CatalogParseStatus.SERVER_UNAVAILABLE,
                            listings=listings,
                            processed_pages=processed_pages,
                            error_state=SERVER_ERROR_5XX_DETECTOR_ID,
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
                            single_page=single_page,
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
                        single_page=single_page,
                    )
                # Пробуем ещё раз

        if not load_success:
            # Не должно происходить, но на всякий случай
            break

    # Успешное завершение
    final_status = (
        CatalogParseStatus.EMPTY
        if processed_pages > 0 and not listings
        else CatalogParseStatus.SUCCESS
    )

    return _build_result(
        status=final_status,
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
        single_page=single_page,
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
    single_page: bool = False,
) -> CatalogParseResult:
    """Собирает CatalogParseResult со всеми полями."""
    meta = CatalogParseMeta(
        status=status,
        processed_pages=processed_pages,
        processed_cards=len(listings),
        last_state=error_state,
        last_url=error_url or resume_url,
    )

    # При single_page=True приватные поля остаются пустыми/дефолтными
    if single_page:
        return CatalogParseResult(
            status=status,
            listings=listings,
            meta=meta,
            error_state=error_state,
            error_url=error_url,
            resume_url=None,
            resume_page_number=None,
            _catalog_url="",
            _fields=set(),
            _max_pages=None,
            _sort=None,
            _start_page=1,
            _include_html=False,
            _max_captcha_attempts=30,
            _load_timeout=180_000,
            _load_retries=5,
            _processed_pages=0,
            _single_page=True,
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
        _single_page=False,
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

    # Продолжаем парсинг с _skip_navigation=True
    # URL не нужен — берётся из page.url (уже содержит параметр f с фильтрами)
    continuation = await parse_catalog(
        new_page,
        url=None,
        fields=prev_result._fields,
        max_pages=remaining_pages,
        sort=prev_result._sort,
        start_page=prev_result.resume_page_number or 1,
        include_html=prev_result._include_html,
        max_captcha_attempts=prev_result._max_captcha_attempts,
        load_timeout=prev_result._load_timeout,
        load_retries=prev_result._load_retries,
        max_srcset_wait_ms=prev_result._max_srcset_wait_ms
            if hasattr(prev_result, "_max_srcset_wait_ms") else 3000,
        _skip_navigation=True,
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
