"""Применение механических фильтров через Playwright."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime

from playwright.async_api import Page

from .constants import (
    TRANSMISSION_SLUGS,
    DRIVE_VALUES,
    SELLER_TYPE_VALUES,
    ENGINE_VOLUMES,
    normalize_value,
)

__all__ = ["apply_mechanical_filters"]

logger = logging.getLogger(__name__)

# Таймауты и задержки (мс)
FILTER_DELAY_MS = 1000
SCROLL_DELAY_MS = 500
SHOW_BUTTON_WAIT_MS = 2000
ELEMENT_TIMEOUT_MS = 10000


async def apply_mechanical_filters(
    page: Page,
    *,
    year_from: int | None = None,
    year_to: int | None = None,
    mileage_from: int | None = None,
    mileage_to: int | None = None,
    engine_volumes: list[float] | None = None,
    transmission: list[str] | None = None,
    drive: list[str] | None = None,
    power_from: int | None = None,
    power_to: int | None = None,
    turbo: bool | None = None,
    seller_type: str | None = None,
) -> str:
    """Применяет механические фильтры на открытой странице каталога.

    Все фильтры заполняются последовательно, кнопка "Показать" нажимается
    ОДИН РАЗ в конце после всех фильтров.

    Args:
        page: Playwright Page с открытым каталогом.
        year_from, year_to: Год выпуска (от/до).
        mileage_from, mileage_to: Пробег в км (от/до).
        engine_volumes: Список объёмов двигателя (например, [2.0, 2.5]).
        transmission: Список типов коробки (например, ["Механика", "Автомат"]).
        drive: Список типов привода (например, ["Полный"]).
        power_from, power_to: Мощность в л.с. (от/до).
        turbo: Наличие турбины (True/False).
        seller_type: Тип продавца ("Дилеры" или "Частные").

    Returns:
        Новый URL страницы после применения фильтров.

    Raises:
        ValueError: Если фильтр не найден на странице или значение невалидно.
    """
    filters_applied = []

    # Ждём загрузки страницы (React рендеринг)
    await page.wait_for_timeout(3000)

    # 1. Год выпуска
    if year_from is not None or year_to is not None:
        await _fill_year(page, year_from, year_to)
        filters_applied.append(f"year={year_from}-{year_to}")

    # 2. Пробег
    if mileage_from is not None or mileage_to is not None:
        await _fill_mileage(page, mileage_from, mileage_to)
        filters_applied.append(f"mileage={mileage_from}-{mileage_to}")

    # 3. Объём двигателя
    if engine_volumes:
        await _fill_engine_volume(page, engine_volumes)
        filters_applied.append(f"engine_volumes={engine_volumes}")

    # 4. Коробка передач (только если 2+ значений)
    if transmission and len(transmission) >= 2:
        await _fill_transmission(page, transmission)
        filters_applied.append(f"transmission={transmission}")

    # 5. Привод
    if drive:
        await _fill_drive(page, drive)
        filters_applied.append(f"drive={drive}")

    # 6. Мощность
    if power_from is not None or power_to is not None:
        await _fill_power(page, power_from, power_to)
        filters_applied.append(f"power={power_from}-{power_to}")

    # 7. Турбина
    if turbo is not None:
        await _fill_turbo(page, turbo)
        filters_applied.append(f"turbo={turbo}")

    # 8. Продавцы
    if seller_type:
        await _fill_seller_type(page, seller_type)
        filters_applied.append(f"seller_type={seller_type}")

    # 9. Кнопка "Показать N объявлений"
    if filters_applied:
        logger.info("Применены механические фильтры: %s", ", ".join(filters_applied))
        await _click_show_button(page)

    return page.url


async def _fill_year(page: Page, year_from: int | None, year_to: int | None) -> None:
    """Заполняет фильтр года выпуска."""
    current_year = datetime.now().year

    if year_from is not None:
        if not (1900 <= year_from <= current_year + 1):
            logger.error("Недопустимое значение year_from=%d", year_from)
            raise ValueError(f"Недопустимое значение year_from={year_from}")

        input_el = page.locator("xpath=//h5[contains(text(),'Год выпуска')]/following::input[1]")
        if not await input_el.count():
            logger.error("Фильтр 'Год выпуска' не найден на странице")
            raise ValueError("Фильтр 'Год выпуска' не найден на странице")

        await input_el.scroll_into_view_if_needed()
        await input_el.click()
        await input_el.fill(str(year_from))
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(FILTER_DELAY_MS)

    if year_to is not None:
        if not (1900 <= year_to <= current_year + 1):
            logger.error("Недопустимое значение year_to=%d", year_to)
            raise ValueError(f"Недопустимое значение year_to={year_to}")

        input_el = page.locator("xpath=//h5[contains(text(),'Год выпуска')]/following::input[2]")
        if not await input_el.count():
            logger.error("Фильтр 'Год выпуска (до)' не найден на странице")
            raise ValueError("Фильтр 'Год выпуска (до)' не найден на странице")

        await input_el.scroll_into_view_if_needed()
        await input_el.click()
        await input_el.fill(str(year_to))
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(FILTER_DELAY_MS)


async def _fill_mileage(page: Page, mileage_from: int | None, mileage_to: int | None) -> None:
    """Заполняет фильтр пробега."""
    if mileage_from is not None:
        if mileage_from < 0:
            logger.error("Недопустимое значение mileage_from=%d", mileage_from)
            raise ValueError(f"Недопустимое значение mileage_from={mileage_from}")

        input_el = page.locator("xpath=//h5[contains(text(),'Пробег')]/following::input[1]")
        if not await input_el.count():
            logger.error("Фильтр 'Пробег' не найден на странице")
            raise ValueError("Фильтр 'Пробег' не найден на странице")

        await input_el.scroll_into_view_if_needed()
        await input_el.click()
        await input_el.fill(str(mileage_from))
        await page.keyboard.press("Tab")
        await page.wait_for_timeout(FILTER_DELAY_MS * 2)

    if mileage_to is not None:
        if mileage_to < 0:
            logger.error("Недопустимое значение mileage_to=%d", mileage_to)
            raise ValueError(f"Недопустимое значение mileage_to={mileage_to}")

        input_el = page.locator("xpath=//h5[contains(text(),'Пробег')]/following::input[2]")
        if not await input_el.count():
            logger.error("Фильтр 'Пробег (до)' не найден на странице")
            raise ValueError("Фильтр 'Пробег (до)' не найден на странице")

        await input_el.scroll_into_view_if_needed()
        await input_el.click()
        await input_el.fill(str(mileage_to))
        await page.keyboard.press("Tab")
        await page.wait_for_timeout(FILTER_DELAY_MS * 2)


async def _fill_engine_volume(page: Page, volumes: list[float]) -> None:
    """Заполняет фильтр объёма двигателя."""
    # Валидация
    for vol in volumes:
        if vol not in ENGINE_VOLUMES:
            logger.error("Недопустимое значение объёма двигателя=%s", vol)
            raise ValueError(
                f"Недопустимое значение объёма двигателя={vol}. "
                f"Допустимые: {', '.join(map(str, ENGINE_VOLUMES[:10]))}..."
            )

    # Открываем dropdown
    input_el = page.locator("xpath=//h5[contains(text(),'Объём двигателя')]/following::input[1]")
    if not await input_el.count():
        logger.error("Фильтр 'Объём двигателя' не найден на странице")
        raise ValueError("Фильтр 'Объём двигателя' не найден на странице")

    await input_el.scroll_into_view_if_needed()
    await input_el.click()
    await page.wait_for_timeout(FILTER_DELAY_MS)

    # Выбираем значения
    for vol in volumes:
        # Формат: всегда "X.X л" (например "2.0 л", "2.5 л")
        vol_str = f"{vol} л"
        checkbox = page.get_by_role("checkbox", name=vol_str)

        try:
            await checkbox.wait_for(state="visible", timeout=ELEMENT_TIMEOUT_MS)
            await checkbox.click()
            await page.wait_for_timeout(SCROLL_DELAY_MS)
        except Exception:
            logger.error("Чекбокс объёма '%s' не найден на странице", vol_str)
            raise ValueError(f"Чекбокс объёма '{vol_str}' не найден на странице")

    await page.keyboard.press("Escape")
    await page.wait_for_timeout(FILTER_DELAY_MS)


async def _fill_transmission(page: Page, values: list[str]) -> None:
    """Заполняет фильтр коробки передач."""
    for value in values:
        canonical = normalize_value(value, TRANSMISSION_SLUGS, "transmission")

        checkbox = page.get_by_label(canonical, exact=True).first
        if not await checkbox.count():
            logger.error("Фильтр коробки передач '%s' не найден на странице", canonical)
            raise ValueError(f"Фильтр коробки передач '{canonical}' не найден на странице")

        await checkbox.scroll_into_view_if_needed()
        await page.wait_for_timeout(SCROLL_DELAY_MS)
        await checkbox.click(force=True)
        await page.wait_for_timeout(FILTER_DELAY_MS)


async def _fill_drive(page: Page, values: list[str]) -> None:
    """Заполняет фильтр привода."""
    for value in values:
        canonical = normalize_value(value, DRIVE_VALUES, "drive")

        checkbox = page.get_by_label(canonical, exact=True).first
        if not await checkbox.count():
            logger.error("Фильтр привода '%s' не найден на странице", canonical)
            raise ValueError(f"Фильтр привода '{canonical}' не найден на странице")

        await checkbox.scroll_into_view_if_needed()
        await page.wait_for_timeout(SCROLL_DELAY_MS)
        await checkbox.click(force=True)
        await page.wait_for_timeout(FILTER_DELAY_MS)


async def _fill_power(page: Page, power_from: int | None, power_to: int | None) -> None:
    """Заполняет фильтр мощности."""
    if power_from is not None:
        if power_from < 1:
            logger.error("Недопустимое значение power_from=%d", power_from)
            raise ValueError(f"Недопустимое значение power_from={power_from}")

        input_el = page.locator("xpath=//h5[contains(text(),'Мощность')]/following::input[1]")
        if not await input_el.count():
            logger.error("Фильтр 'Мощность' не найден на странице")
            raise ValueError("Фильтр 'Мощность' не найден на странице")

        await input_el.scroll_into_view_if_needed()
        await input_el.wait_for(state="visible", timeout=ELEMENT_TIMEOUT_MS)
        await input_el.click()
        await input_el.fill(str(power_from))
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(FILTER_DELAY_MS)

    if power_to is not None:
        if power_to < 1:
            logger.error("Недопустимое значение power_to=%d", power_to)
            raise ValueError(f"Недопустимое значение power_to={power_to}")

        input_el = page.locator("xpath=//h5[contains(text(),'Мощность')]/following::input[2]")
        if not await input_el.count():
            logger.error("Фильтр 'Мощность (до)' не найден на странице")
            raise ValueError("Фильтр 'Мощность (до)' не найден на странице")

        await input_el.scroll_into_view_if_needed()
        await input_el.click()
        await input_el.fill(str(power_to))
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(FILTER_DELAY_MS)


async def _fill_turbo(page: Page, has_turbo: bool) -> None:
    """Заполняет фильтр турбины."""
    label_text = "Есть" if has_turbo else "Нет"

    turbo_option = page.locator(
        f"xpath=//h5[contains(text(),'Турбина')]/following::label[contains(.,'{label_text}')]"
    ).first

    if not await turbo_option.count():
        logger.error("Фильтр турбины '%s' не найден на странице", label_text)
        raise ValueError(f"Фильтр турбины '{label_text}' не найден на странице")

    await turbo_option.scroll_into_view_if_needed()
    await turbo_option.wait_for(state="visible", timeout=ELEMENT_TIMEOUT_MS)
    await turbo_option.click()
    await page.wait_for_timeout(FILTER_DELAY_MS)


async def _fill_seller_type(page: Page, seller_type: str) -> None:
    """Заполняет фильтр типа продавца."""
    canonical = normalize_value(seller_type, SELLER_TYPE_VALUES, "seller_type")

    # "Все" — это default, ничего не делаем
    if canonical == "Все":
        return

    seller_option = page.locator(
        f"xpath=//h5[contains(text(),'Продавцы')]/following::span[contains(.,'{canonical}')]"
    ).first

    if not await seller_option.count():
        logger.error("Фильтр продавца '%s' не найден на странице", canonical)
        raise ValueError(f"Фильтр продавца '{canonical}' не найден на странице")

    await seller_option.scroll_into_view_if_needed()
    await seller_option.click()
    await page.wait_for_timeout(FILTER_DELAY_MS)


async def _click_show_button(page: Page) -> None:
    """Находит и кликает кнопку 'Показать N объявлений'."""
    await page.wait_for_timeout(SHOW_BUTTON_WAIT_MS)

    buttons = page.locator("button")
    count = await buttons.count()

    for i in range(count):
        btn = buttons.nth(i)
        try:
            text = await btn.text_content(timeout=500)
            if not text:
                continue

            # Ищем "Показать" + число + "объявлен", но НЕ "телефон"
            if "Показать" in text and "телефон" not in text.lower():
                if re.search(r"\d+.*объявлен", text):
                    await btn.scroll_into_view_if_needed()

                    async with page.expect_navigation(timeout=15000):
                        await btn.click()

                    logger.info("Кликнули кнопку: %s", text.strip()[:50])
                    return
        except Exception:
            continue

    logger.error("Кнопка 'Показать N объявлений' не найдена на странице")
    raise ValueError("Кнопка 'Показать N объявлений' не найдена на странице")
