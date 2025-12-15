"""Навигация по страницам каталога Avito."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlparse

from playwright.async_api import Page, Response

from .helpers import apply_sort, apply_start_page

__all__ = ["navigate_to_catalog"]


async def navigate_to_catalog(
    page: Page,
    catalog_url: str,
    *,
    sort: str | None = None,
    start_page: int = 1,
    timeout: int = 180_000,
    wait_until: str = "domcontentloaded",
) -> Response:
    """Переходит на страницу каталога с параметрами сортировки и пагинации.

    Это обёртка над page.goto(), которая:
    1. Проверяет, есть ли уже параметры ?p= и ?s= в URL
    2. Применяет сортировку и пагинацию только если их нет
    3. Выполняет переход на страницу

    Не делает:
    - Не решает капчу
    - Не детектирует состояние страницы
    - Не парсит карточки

    Args:
        page: Playwright Page объект.
        catalog_url: URL каталога. Может уже содержать query-параметры
            (например, next_url от кнопки пагинации).
        sort: Тип сортировки: "date", "price_asc", "price_desc", "mileage_asc"
            или None для сортировки по умолчанию.
        start_page: Номер страницы (>=1). По умолчанию 1.
        timeout: Таймаут загрузки в миллисекундах. По умолчанию 180000 (3 минуты).
        wait_until: Событие ожидания загрузки. По умолчанию "domcontentloaded".

    Returns:
        Response объект от page.goto().

    Raises:
        TimeoutError: Если страница не загрузилась за отведённое время.
        ValueError: Если передана неизвестная сортировка.

    Examples:
        # Первый переход на каталог
        >>> response = await navigate_to_catalog(
        ...     page,
        ...     "https://avito.ru/moskva/telefony",
        ...     sort="date",
        ...     start_page=1,
        ... )

        # Переход на следующую страницу (URL уже содержит параметры)
        >>> next_url = "https://avito.ru/moskva/telefony?p=2&s=104"
        >>> response = await navigate_to_catalog(page, next_url)
    """
    # Проверяем, есть ли уже параметры в URL
    parsed = urlparse(catalog_url)
    existing_params = dict(parse_qsl(parsed.query))

    # Применяем сортировку только если её нет в URL
    final_url = catalog_url
    if sort is not None and "s" not in existing_params:
        final_url = apply_sort(final_url, sort)

    # Применяем пагинацию только если её нет в URL и start_page > 1
    if start_page > 1 and "p" not in existing_params:
        final_url = apply_start_page(final_url, start_page)

    return await page.goto(
        final_url,
        wait_until=wait_until,
        timeout=timeout,
    )
