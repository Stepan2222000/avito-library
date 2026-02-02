"""Построение и парсинг URL каталога Avito с фильтрами."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .constants import (
    SORT_PARAMS,
    BODY_TYPE_SLUGS,
    FUEL_TYPE_SLUGS,
    TRANSMISSION_SLUGS,
    CONDITION_SLUGS,
    RADIUS_VALUES,
    ALL_FILTER_SLUGS,
    BODY_TYPE_SLUGS_REVERSE,
    FUEL_TYPE_SLUGS_REVERSE,
    TRANSMISSION_SLUGS_REVERSE,
    CONDITION_SLUGS_REVERSE,
    normalize_value,
)

__all__ = [
    "build_catalog_url",
    "parse_catalog_url",
    "merge_url_with_params",
]

# Паттерн для base64 хвоста в URL (например, sedan-ASgBAgIC...)
_BASE64_TAIL_RE = re.compile(r"-[A-Za-z0-9+/=]{10,}$")


def build_catalog_url(
    *,
    city: str | None = None,
    category: str,
    brand: str | None = None,
    model: str | None = None,
    body_type: str | None = None,
    fuel_type: str | None = None,
    transmission: str | None = None,
    condition: str | None = None,
    price_min: int | None = None,
    price_max: int | None = None,
    radius: int | None = None,
    sort: str | None = None,
) -> str:
    """Строит URL каталога Avito с ЧПУ-сегментами и GET-параметрами.

    Args:
        city: Slug города. None = "all" (все регионы).
        category: Slug категории (обязательный!).
        brand: Slug марки.
        model: Slug модели.
        body_type: Тип кузова (русский, например "Седан").
        fuel_type: Тип топлива (русский, например "Бензин").
        transmission: Тип коробки (русский, ТОЛЬКО ОДНО значение!).
        condition: Состояние (русский, "С пробегом" или "Новый").
        price_min: Минимальная цена.
        price_max: Максимальная цена.
        radius: Радиус поиска (0, 50, 100, 200, 300, 500 км).
        sort: Сортировка ("date", "price_asc", "price_desc", "mileage_asc").

    Returns:
        Готовый URL каталога.

    Raises:
        ValueError: При невалидных значениях параметров.
    """
    # Собираем path сегменты
    segments = [city or "all", category]

    if brand:
        segments.append(brand)
    if model:
        segments.append(model)
    if body_type:
        canonical = normalize_value(body_type, BODY_TYPE_SLUGS, "body_type")
        segments.append(BODY_TYPE_SLUGS[canonical])
    if fuel_type:
        canonical = normalize_value(fuel_type, FUEL_TYPE_SLUGS, "fuel_type")
        segments.append(FUEL_TYPE_SLUGS[canonical])
    if transmission:
        canonical = normalize_value(transmission, TRANSMISSION_SLUGS, "transmission")
        segments.append(TRANSMISSION_SLUGS[canonical])
    if condition:
        canonical = normalize_value(condition, CONDITION_SLUGS, "condition")
        segments.append(CONDITION_SLUGS[canonical])

    # GET-параметры
    query_params: dict[str, str] = {}

    if price_min is not None:
        query_params["pmin"] = str(price_min)
    if price_max is not None:
        query_params["pmax"] = str(price_max)
    if radius is not None:
        if radius not in RADIUS_VALUES:
            raise ValueError(
                f"Недопустимое значение radius={radius}. "
                f"Допустимые: {', '.join(map(str, RADIUS_VALUES))}"
            )
        query_params["radius"] = str(radius)
    if sort is not None:
        if sort not in SORT_PARAMS:
            raise ValueError(
                f"Недопустимое значение sort={sort!r}. "
                f"Допустимые: {', '.join(SORT_PARAMS.keys())}"
            )
        query_params["s"] = SORT_PARAMS[sort]

    path = "/" + "/".join(segments)
    query_string = urlencode(query_params) if query_params else ""
    return urlunparse(("https", "www.avito.ru", path, "", query_string, ""))


def parse_catalog_url(url: str) -> dict:
    """Парсит URL каталога Avito, извлекает параметры фильтрации.

    Args:
        url: URL каталога Avito.

    Returns:
        Словарь с извлечёнными параметрами:
        - city, category, brand, model — строки или None
        - body_type, fuel_type, transmission, condition — русские названия или None
        - price_min, price_max, radius — int или None
        - sort — ключ сортировки или None
        - page — номер страницы или None
        - query — поисковый запрос (?q=) или None
    """
    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.split("/") if p]

    result: dict = {
        "city": None,
        "category": None,
        "brand": None,
        "model": None,
        "body_type": None,
        "fuel_type": None,
        "transmission": None,
        "condition": None,
        "price_min": None,
        "price_max": None,
        "radius": None,
        "sort": None,
        "page": None,
        "query": None,
    }

    if not path_parts:
        return result

    result["city"] = path_parts[0]

    if len(path_parts) > 1:
        result["category"] = path_parts[1]

    # Остальные сегменты — фильтры
    for segment in path_parts[2:]:
        clean = _BASE64_TAIL_RE.sub("", segment)  # Убираем base64 хвост

        segment_type = ALL_FILTER_SLUGS.get(clean)
        if segment_type == "body_type":
            result["body_type"] = BODY_TYPE_SLUGS_REVERSE[clean]
        elif segment_type == "fuel_type":
            result["fuel_type"] = FUEL_TYPE_SLUGS_REVERSE[clean]
        elif segment_type == "transmission":
            result["transmission"] = TRANSMISSION_SLUGS_REVERSE[clean]
        elif segment_type == "condition":
            result["condition"] = CONDITION_SLUGS_REVERSE[clean]
        elif result["brand"] is None:
            result["brand"] = clean
        elif result["model"] is None:
            result["model"] = clean

    # GET-параметры
    query_params = dict(parse_qsl(parsed.query))

    if "pmin" in query_params:
        try:
            result["price_min"] = int(query_params["pmin"])
        except ValueError:
            pass

    if "pmax" in query_params:
        try:
            result["price_max"] = int(query_params["pmax"])
        except ValueError:
            pass

    if "radius" in query_params:
        try:
            result["radius"] = int(query_params["radius"])
        except ValueError:
            pass

    if "s" in query_params:
        sort_value = query_params["s"]
        for key, val in SORT_PARAMS.items():
            if val == sort_value:
                result["sort"] = key
                break

    if "p" in query_params:
        try:
            result["page"] = int(query_params["p"])
        except ValueError:
            pass

    if "q" in query_params:
        result["query"] = query_params["q"]

    return result


def merge_url_with_params(
    url: str,
    *,
    city: str | None = None,
    category: str | None = None,
    brand: str | None = None,
    model: str | None = None,
    body_type: str | None = None,
    fuel_type: str | None = None,
    transmission: str | None = None,
    condition: str | None = None,
    price_min: int | None = None,
    price_max: int | None = None,
    radius: int | None = None,
    sort: str | None = None,
) -> tuple[dict, str]:
    """Объединяет параметры из URL с переданными параметрами.

    Проверяет конфликты: если в URL есть значение X, а передано Y — ValueError.

    Поддерживает URL без категории, если есть поисковый запрос (?q=).
    В этом случае URL не перестраивается, а только дополняется GET-параметрами.

    Returns:
        Кортеж (merged_params, final_url).

    Raises:
        ValueError: При конфликте параметров или отсутствии category/query.
    """
    url_params = parse_catalog_url(url)

    param_pairs = [
        ("city", city, url_params["city"]),
        ("category", category, url_params["category"]),
        ("brand", brand, url_params["brand"]),
        ("model", model, url_params["model"]),
        ("body_type", body_type, url_params["body_type"]),
        ("fuel_type", fuel_type, url_params["fuel_type"]),
        ("transmission", transmission, url_params["transmission"]),
        ("condition", condition, url_params["condition"]),
        ("price_min", price_min, url_params["price_min"]),
        ("price_max", price_max, url_params["price_max"]),
        ("radius", radius, url_params["radius"]),
        ("sort", sort, url_params["sort"]),
    ]

    merged: dict = {}

    for param_name, passed_value, url_value in param_pairs:
        if passed_value is not None and url_value is not None:
            # Для строк сравниваем в lower
            if isinstance(passed_value, str) and isinstance(url_value, str):
                if passed_value.lower() != url_value.lower():
                    raise ValueError(
                        f"Конфликт параметра {param_name}: "
                        f"в URL={url_value!r}, передано={passed_value!r}"
                    )
            elif passed_value != url_value:
                raise ValueError(
                    f"Конфликт параметра {param_name}: "
                    f"в URL={url_value!r}, передано={passed_value!r}"
                )
            merged[param_name] = passed_value
        elif passed_value is not None:
            merged[param_name] = passed_value
        else:
            merged[param_name] = url_value

    # Сохраняем query из URL
    merged["query"] = url_params.get("query")

    # Проверяем: нужна либо category, либо query
    has_category = merged.get("category") is not None
    has_query = merged.get("query") is not None

    if not has_category and not has_query:
        raise ValueError(
            "Требуется либо category в URL/параметрах, "
            "либо поисковый запрос (?q=) в URL"
        )

    # Если есть категория — строим URL через build_catalog_url
    if has_category:
        final_url = build_catalog_url(
            city=merged.get("city"),
            category=merged["category"],
            brand=merged.get("brand"),
            model=merged.get("model"),
            body_type=merged.get("body_type"),
            fuel_type=merged.get("fuel_type"),
            transmission=merged.get("transmission"),
            condition=merged.get("condition"),
            price_min=merged.get("price_min"),
            price_max=merged.get("price_max"),
            radius=merged.get("radius"),
            sort=merged.get("sort"),
        )
    else:
        # Нет категории, но есть query — модифицируем оригинальный URL
        final_url = _add_get_params_to_url(
            url,
            price_min=merged.get("price_min"),
            price_max=merged.get("price_max"),
            radius=merged.get("radius"),
            sort=merged.get("sort"),
        )

    return merged, final_url


def _add_get_params_to_url(
    url: str,
    *,
    price_min: int | None = None,
    price_max: int | None = None,
    radius: int | None = None,
    sort: str | None = None,
) -> str:
    """Добавляет GET-параметры к существующему URL.

    Используется для URL без категории (поисковые запросы),
    где нельзя перестроить URL через build_catalog_url().
    """
    parsed = urlparse(url)
    query_params = dict(parse_qsl(parsed.query, keep_blank_values=True))

    if price_min is not None and "pmin" not in query_params:
        query_params["pmin"] = str(price_min)
    if price_max is not None and "pmax" not in query_params:
        query_params["pmax"] = str(price_max)
    if radius is not None and "radius" not in query_params:
        if radius not in RADIUS_VALUES:
            raise ValueError(
                f"Недопустимое значение radius={radius}. "
                f"Допустимые: {', '.join(map(str, RADIUS_VALUES))}"
            )
        query_params["radius"] = str(radius)
    if sort is not None and "s" not in query_params:
        if sort not in SORT_PARAMS:
            raise ValueError(
                f"Недопустимое значение sort={sort!r}. "
                f"Допустимые: {', '.join(SORT_PARAMS.keys())}"
            )
        query_params["s"] = SORT_PARAMS[sort]

    new_query = urlencode(query_params, doseq=True)
    return urlunparse(parsed._replace(query=new_query))
