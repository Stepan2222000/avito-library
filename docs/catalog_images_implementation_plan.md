# План реализации: Парсинг изображений из каталога Avito

**Дата создания:** 2026-01-30
**Статус:** Готов к реализации

---

## 1. Цель

Добавить возможность парсинга изображений из карточек каталога Avito (без захода на страницу объявления).

**Что получим:**
- Поле `images` в `CatalogListing` со скачанными изображениями
- Поле `images_urls` с URL изображений
- Поле `images_errors` с ошибками скачивания
- Общий модуль `image_downloader.py` для переиспользования в card_parser и catalog_parser

---

## 2. Исследование HTML-структуры

### 2.1 Источник изображений

В каталоге изображения находятся в `<img srcset>` внутри карточек:

```html
<div data-marker="item" data-item-id="7449928153">
  <div class="photo-slider-photoSlider-*">
    <ul class="photo-slider-list-*">
      <li data-marker="slider-image/image-{url}">
        <div class="photo-slider-item-*">
          <img class="photo-slider-image-*"
               srcset="url_208w 208w, url_236w 236w, url_318w 318w, url_416w 416w, url_472w 472w, url_636w 636w"
               src="url_208w">
        </div>
      </li>
    </ul>
  </div>
</div>
```

### 2.2 Формат srcset

- **Всегда 6 размеров:** 208w, 236w, 318w, 416w, 472w, 636w
- **Максимальное качество:** 636w (~636 пикселей ширина)
- **Формат записи:** `"{url} {width}w"`, разделитель `,`

### 2.3 Статистика (из живого теста)

| Метрика | Значение |
|---------|----------|
| Карточек с изображениями | 26% |
| Карточек без изображений | 74% (услуги, объявления без фото) |
| Максимум фото на карточку | 5 |
| Среднее фото на карточку | ~2.5 |

### 2.4 Селектор

```css
/* Универсальный селектор для изображений товаров */
img[srcset]

/* НЕ захватывает логотипы продавцов (у них нет srcset) */
```

---

## 3. Сравнение с card_parser

| Аспект | Card Parser | Catalog Parser |
|--------|-------------|----------------|
| Источник URL | JSON `__preloadedState__` | HTML `srcset` |
| Качество | 1280x960 | 636w |
| Библиотека | BeautifulSoup | Playwright Locator |
| Скачивание | `_download_images()` | Переиспользуем |

**Вывод:** Логика скачивания идентична, логика извлечения URL — разная.

---

## 4. Архитектурные решения

### 4.1 Общий модуль для скачивания

**Создать:** `avito_library/utils/image_downloader.py`

**Перенести из card_parser.py:**
- Константы: `MAX_IMAGE_SIZE`, `RETRY_DELAYS`, `CHUNK_SIZE`, `RETRYABLE_STATUS_CODES`
- Функция: `_validate_image()` → `validate_image()`
- Функция: `_download_images()` → `download_images()`

**Причины:**
- DRY — не дублировать код
- Тестируемость — можно тестировать отдельно
- Переиспользование — пользователи могут импортировать

### 4.2 Новые поля в CatalogListing

```python
@dataclass(slots=True)
class CatalogListing:
    # ... существующие поля ...
    raw_html: str | None

    # Новые поля (добавить в конец):
    images: list[bytes] | None = None
    images_urls: list[str] | None = None
    images_errors: list[str] | None = None
```

### 4.3 Логика извлечения в helpers.py

```python
async def _extract_images_from_catalog_card(card: Locator) -> list[str]:
    """Извлекает URL изображений максимального качества из srcset."""
    urls = []

    imgs = await card.locator('img[srcset]').all()

    for img in imgs:
        srcset = await img.get_attribute('srcset')
        if not srcset:
            continue

        # Парсим srcset, берём URL максимального размера
        parts = srcset.split(',')
        best_url = None
        best_size = 0

        for part in parts:
            trimmed = part.strip()
            last_space = trimmed.rfind(' ')
            if last_space == -1:
                continue

            url = trimmed[:last_space]
            size_str = trimmed[last_space + 1:]

            try:
                size = int(size_str.rstrip('w'))
            except ValueError:
                continue

            if size > best_size:
                best_size = size
                best_url = url

        if best_url:
            urls.append(best_url)

    return urls
```

### 4.4 Интеграция в extract_listing

```python
async def extract_listing(
    card: Locator,
    fields: set[str],
    *,
    include_html: bool,
) -> CatalogListing:
    # ... существующий код ...

    # Инициализация новых полей
    images: list[bytes] | None = None
    images_urls: list[str] | None = None
    images_errors: list[str] | None = None

    # Извлечение изображений (если запрошено)
    if "images" in fields:
        urls = await _extract_images_from_catalog_card(card)
        images_urls = urls
        if urls:
            images, images_errors = await download_images(urls)
        else:
            images = []
            images_errors = []

    return CatalogListing(
        # ... существующие поля ...
        images=images,
        images_urls=images_urls,
        images_errors=images_errors,
    )
```

---

## 5. Детальный план реализации

### Шаг 1: Создать utils/image_downloader.py

**Файл:** `avito_library/utils/image_downloader.py`

**Содержимое:**
```python
"""Утилиты для скачивания изображений."""

import asyncio
from typing import Optional

import httpx

# Константы
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB
RETRY_DELAYS = (1.0, 2.0, 4.0)
CHUNK_SIZE = 8192
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def validate_image(data: bytes) -> bool:
    """Проверка magic bytes изображения (JPEG, PNG, WebP, GIF)."""
    # ... код из card_parser.py ...


async def download_images(
    urls: list[str],
    max_concurrent: int = 10,
    timeout: float = 15.0,
) -> tuple[list[bytes], list[str]]:
    """Параллельно скачивает изображения."""
    # ... код из card_parser.py ...
```

### Шаг 2: Обновить utils/__init__.py

```python
from .continue_button import press_continue_and_detect
from .image_downloader import download_images, validate_image

__all__ = [
    "press_continue_and_detect",
    "download_images",
    "validate_image",
]
```

### Шаг 3: Обновить card_parser.py

```python
# Заменить локальные функции на импорт
from avito_library.utils.image_downloader import (
    download_images as _download_images,
    validate_image as _validate_image,
    MAX_IMAGE_SIZE as _MAX_IMAGE_SIZE,
    RETRY_DELAYS as _RETRY_DELAYS,
    CHUNK_SIZE as _CHUNK_SIZE,
    RETRYABLE_STATUS_CODES as _RETRYABLE_STATUS_CODES,
)

# Удалить локальные определения констант и функций
```

### Шаг 4: Обновить catalog_parser/models.py

```python
@dataclass(slots=True)
class CatalogListing:
    item_id: str
    title: str | None
    price: int | None
    snippet_text: str | None
    location_city: str | None
    location_area: str | None
    location_extra: str | None
    seller_name: str | None
    seller_id: str | None
    seller_rating: float | None
    seller_reviews: int | None
    promoted: bool
    published_ago: str | None
    raw_html: str | None
    # Новые поля:
    images: list[bytes] | None = None
    images_urls: list[str] | None = None
    images_errors: list[str] | None = None
```

### Шаг 5: Обновить catalog_parser/helpers.py

**Импорты:**
```python
from ...utils.image_downloader import download_images
```

**Новая функция:**
```python
async def _extract_images_from_catalog_card(card: Locator) -> list[str]:
    """Извлекает URL изображений максимального качества из srcset."""
    # ... реализация выше ...
```

**Обновить extract_listing:**
```python
# Добавить инициализацию
images: list[bytes] | None = None
images_urls: list[str] | None = None
images_errors: list[str] | None = None

# Добавить блок извлечения
if "images" in fields:
    urls = await _extract_images_from_catalog_card(card)
    images_urls = urls
    if urls:
        images, images_errors = await download_images(urls)
    else:
        images = []
        images_errors = []

# Обновить return
return CatalogListing(
    # ... существующие поля ...
    images=images,
    images_urls=images_urls,
    images_errors=images_errors,
)
```

### Шаг 6: Обновить README.md

**Раздел CatalogListing (строка ~636):**
```markdown
| `images` | `list[bytes] \| None` | Скачанные изображения |
| `images_urls` | `list[str] \| None` | URL изображений |
| `images_errors` | `list[str] \| None` | Ошибки скачивания |
```

**Раздел fields (строка ~342):**
```markdown
`fields` | `Iterable[str]` | Поля для извлечения: `item_id`, `title`, `price`, `snippet_text`, `location_city`, `location_area`, `location_extra`, `seller_name`, `seller_id`, `seller_rating`, `seller_reviews`, `promoted`, `published_ago`, `raw_html`, **`images`**
```

**Добавить пример:**
```python
# Парсинг с изображениями
result = await parse_catalog(
    page,
    category="telefony",
    city="moskva",
    fields=["item_id", "title", "price", "images"],
    max_pages=1,
)

for listing in result.listings:
    print(f"{listing.title}: {len(listing.images or [])} фото")
```

### Шаг 7: Тестирование

```python
# Минимальный тест в терминале
import asyncio
from playwright.async_api import async_playwright
from avito_library import parse_catalog

async def test():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        page = await browser.new_page()

        result = await parse_catalog(
            page,
            url="https://www.avito.ru/all?cd=1&q=a0002342612",
            fields=["item_id", "title", "images"],
            single_page=True,
        )

        print(f"Статус: {result.status}")
        print(f"Карточек: {len(result.listings)}")

        with_images = [l for l in result.listings if l.images]
        print(f"С изображениями: {len(with_images)}")

        if with_images:
            first = with_images[0]
            print(f"Первая карточка: {first.title}")
            print(f"  URL: {first.images_urls}")
            print(f"  Фото: {len(first.images)} шт")
            print(f"  Ошибок: {len(first.images_errors or [])}")

        await browser.close()

asyncio.run(test())
```

---

## 6. Edge Cases

| Случай | Поведение |
|--------|-----------|
| Карточка без фото | `images=[]`, `images_urls=[]`, `images_errors=[]` |
| Ошибка скачивания 1 из 5 | `images` содержит 4, `images_errors` содержит 1 |
| `"images" not in fields` | `images=None`, `images_urls=None`, `images_errors=None` |
| Пустой srcset | Пропускается |
| srcset без размеров | Пропускается |

---

## 7. Производительность

**Оценка для страницы с 50 карточками:**
- Карточек с фото: ~13
- Изображений: ~33
- Размер данных: ~2-3 MB
- Дополнительное время: ~2-3 сек

**Оптимизации (уже реализованы в download_images):**
- Параллельное скачивание (`max_concurrent=10`)
- Streaming с лимитом размера
- Retry при ошибках сервера

---

## 8. Обратная совместимость

**Изменения обратно совместимы:**
- Новые поля имеют `= None` по умолчанию
- Существующий код продолжит работать
- Поля появятся только если запросить `"images"` в `fields`

---

## 9. Файлы для изменения

| Файл | Изменение |
|------|-----------|
| `utils/image_downloader.py` | **СОЗДАТЬ** — общий модуль скачивания |
| `utils/__init__.py` | Добавить экспорты |
| `parsers/card_parser.py` | Импортировать из utils |
| `parsers/catalog_parser/models.py` | Добавить 3 поля в CatalogListing |
| `parsers/catalog_parser/helpers.py` | Добавить извлечение и скачивание |
| `README.md` | Документировать новые поля |

---

## 10. Готовность к реализации

**Все вопросы решены:**
- ✅ Структура HTML изучена
- ✅ Селектор протестирован
- ✅ Алгоритм извлечения работает
- ✅ Архитектура определена
- ✅ План шагов готов

**Можно приступать к реализации.**
