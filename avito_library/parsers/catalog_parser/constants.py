"""Константы и справочники для фильтрации каталога Avito."""

from __future__ import annotations

__all__ = [
    # Сортировка
    "SORT_PARAMS",
    # Справочники для URL (русский → slug)
    "BODY_TYPE_SLUGS",
    "FUEL_TYPE_SLUGS",
    "TRANSMISSION_SLUGS",
    # Допустимые значения для механических фильтров
    "DRIVE_VALUES",
    "SELLER_TYPE_VALUES",
    "ENGINE_VOLUMES",
    "RADIUS_VALUES",
    # Инверсные словари (для парсинга URL)
    "ALL_FILTER_SLUGS",
    "BODY_TYPE_SLUGS_REVERSE",
    "FUEL_TYPE_SLUGS_REVERSE",
    "TRANSMISSION_SLUGS_REVERSE",
    # Функция нормализации
    "normalize_value",
]


# =============================================================================
# СОРТИРОВКА (GET-параметр ?s=)
# =============================================================================

SORT_PARAMS: dict[str, str] = {
    "date": "104",
    "price_asc": "1",
    "price_desc": "2",
    "mileage_asc": "2687_asc",
}


# =============================================================================
# ЧПУ-СЕГМЕНТЫ (русский → slug для URL)
# =============================================================================

BODY_TYPE_SLUGS: dict[str, str] = {
    "Седан": "sedan",
    "Хэтчбек": "hetchbek",
    "Универсал": "universal",
    "Внедорожник": "vnedorozhnik",
    "Кроссовер": "krossover",
    "Купе": "kupe",
    "Кабриолет": "kabriolet",
    "Пикап": "pikap",
    "Минивэн": "miniven",
    "Лимузин": "limuzin",
    "Фургон": "furgon",
}

FUEL_TYPE_SLUGS: dict[str, str] = {
    "Бензин": "benzin",
    "Дизель": "dizel",
    "Электро": "elektro",
    "Гибрид": "gibrid",
    "Газ": "gaz",
}

TRANSMISSION_SLUGS: dict[str, str] = {
    "Механика": "mekhanika",  # ВАЖНО: с буквой "х"!
    "Автомат": "avtomat",
    "Робот": "robot",
    "Вариатор": "variator",
}

# =============================================================================
# ДОПУСТИМЫЕ ЗНАЧЕНИЯ ДЛЯ МЕХАНИЧЕСКИХ ФИЛЬТРОВ
# =============================================================================

DRIVE_VALUES: tuple[str, ...] = (
    "Передний",
    "Задний",
    "Полный",
)

SELLER_TYPE_VALUES: tuple[str, ...] = (
    "Все",
    "Дилеры",
    "Частные",
)

ENGINE_VOLUMES: tuple[float, ...] = (
    0.6, 0.7, 0.8, 0.9,
    1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9,
    2.0, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9,
    3.0, 3.1, 3.2, 3.3, 3.4, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0,
)

RADIUS_VALUES: tuple[int, ...] = (
    0,    # Только указанный город
    50,   # 50 км
    100,  # 100 км
    200,  # 200 км
    300,  # 300 км
    500,  # 500 км
)


# =============================================================================
# ИНВЕРСНЫЕ СЛОВАРИ (slug → русский) — для парсинга URL
# =============================================================================

BODY_TYPE_SLUGS_REVERSE: dict[str, str] = {v: k for k, v in BODY_TYPE_SLUGS.items()}
FUEL_TYPE_SLUGS_REVERSE: dict[str, str] = {v: k for k, v in FUEL_TYPE_SLUGS.items()}
TRANSMISSION_SLUGS_REVERSE: dict[str, str] = {v: k for k, v in TRANSMISSION_SLUGS.items()}
# Объединённый словарь всех slug-ов для определения типа сегмента
ALL_FILTER_SLUGS: dict[str, str] = {
    **{slug: "body_type" for slug in BODY_TYPE_SLUGS.values()},
    **{slug: "fuel_type" for slug in FUEL_TYPE_SLUGS.values()},
    **{slug: "transmission" for slug in TRANSMISSION_SLUGS.values()},
}


# =============================================================================
# ФУНКЦИЯ НОРМАЛИЗАЦИИ
# =============================================================================

# Предвычисленные словари для быстрой нормализации (lowercase → канонический)
_LOWERCASE_MAPS: dict[int, dict[str, str]] = {
    id(BODY_TYPE_SLUGS): {k.lower(): k for k in BODY_TYPE_SLUGS},
    id(FUEL_TYPE_SLUGS): {k.lower(): k for k in FUEL_TYPE_SLUGS},
    id(TRANSMISSION_SLUGS): {k.lower(): k for k in TRANSMISSION_SLUGS},
    id(DRIVE_VALUES): {v.lower(): v for v in DRIVE_VALUES},
    id(SELLER_TYPE_VALUES): {v.lower(): v for v in SELLER_TYPE_VALUES},
}


def normalize_value(
    value: str,
    valid_values: dict[str, str] | tuple[str, ...],
    param_name: str,
) -> str:
    """Нормализует значение к каноническому регистру.

    Args:
        value: Значение для нормализации (например, 'седан', 'СЕДАН').
        valid_values: Словарь или кортеж допустимых значений.
        param_name: Имя параметра для сообщения об ошибке.

    Returns:
        Каноническое значение (например, 'Седан').

    Raises:
        ValueError: Если значение не найдено среди допустимых.
    """
    lower_map = _LOWERCASE_MAPS.get(id(valid_values))
    if lower_map is None:
        # Для неизвестного словаря строим на лету
        if isinstance(valid_values, dict):
            lower_map = {k.lower(): k for k in valid_values}
        else:
            lower_map = {v.lower(): v for v in valid_values}

    lower_value = value.lower()
    if lower_value in lower_map:
        return lower_map[lower_value]

    valid_list = list(valid_values.keys()) if isinstance(valid_values, dict) else list(valid_values)
    raise ValueError(
        f"Недопустимое значение {param_name}={value!r}. "
        f"Допустимые: {', '.join(valid_list)}"
    )
