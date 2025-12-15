# AVITO-LIBRARY: Карта проекта

**Playwright-библиотека для асинхронного парсинга Avito**

- **Версия:** 0.1.1
- **Python:** >=3.11
- **Зависимости:** playwright, beautifulsoup4, lxml, numpy, opencv-python, asyncpg

---

## Структура проекта

```
avito-library/
├── pyproject.toml                          # Конфигурация пакета
├── README.md                               # Документация
├── CLAUDE.md                               # Карта проекта (этот файл)
│
└── avito_library/
    ├── __init__.py                         # Главный API (экспорт всех компонентов)
    ├── config.py                           # Глобальная конфигурация
    ├── install_browser.py                  # CLI для установки Chromium
    │
    ├── detectors/                          # Детекторы состояния страницы
    │   ├── __init__.py                     # Реестр детекторов
    │   ├── detect_page_state.py            # Роутер детектирования
    │   ├── catalog_page_detector.py        # Детектор каталога
    │   ├── card_found_detector.py          # Детектор карточки
    │   ├── seller_profile_detector.py      # Детектор профиля продавца
    │   ├── captcha_geetest_detector.py     # Детектор Geetest-капчи
    │   ├── continue_button_detector.py     # Детектор кнопки "Продолжить"
    │   ├── proxy_block_403_detector.py     # Детектор блокировки 403
    │   ├── proxy_block_429_detector.py     # Детектор rate-limit 429
    │   ├── proxy_auth_407_detector.py      # Детектор требования авторизации 407
    │   └── removed_or_not_found_detector.py # Детектор удалённого объявления
    │
    ├── parsers/                            # Парсеры Avito
    │   ├── __init__.py                     # Экспорт card_parser
    │   ├── card_parser.py                  # Парсер карточки объявления
    │   ├── seller_profile_parser.py        # Парсер профиля продавца
    │   │
    │   └── catalog_parser/                 # Подпакет парсера каталога
    │       ├── __init__.py                 # Экспорт функций
    │       ├── catalog_parser.py           # Основной парсер каталога
    │       ├── models.py                   # Модели данных
    │       ├── helpers.py                  # Вспомогательные функции
    │       └── steam.py                    # Оркестратор повторных попыток
    │
    ├── capcha/                             # Решение Geetest-капчи
    │   ├── __init__.py                     # Экспорт компонентов
    │   ├── resolver.py                     # Оркестратор решения капчи
    │   ├── solve_slider_once.py            # Решение одного слайдера
    │   ├── solver_utils.py                 # OpenCV-вычисления
    │   ├── cache_manager.py                # Менеджер кеша смещений
    │   └── cache_io.py                     # I/O кеша (JSON/PostgreSQL)
    │
    ├── utils/                              # Высокоуровневые утилиты
    │   ├── __init__.py
    │   └── continue_button.py              # Нажатие кнопки и детектирование
    │
    ├── reuse_utils/                        # Переиспользуемые компоненты
    │   ├── __init__.py
    │   ├── task_queue.py                   # FIFO-очередь задач с retry
    │   └── proxy_pool.py                   # Кольцевой пул прокси
    │
    ├── debug/                              # Отладочные утилиты
    │   ├── __init__.py
    │   └── screenshot.py                   # Сохранение скриншотов
    │
    └── data/                               # Данные и кеши
        ├── geetest_cache.json              # Кеш смещений капчи
        ├── geetest_cache2.json             # Альтернативный кеш
        └── db_data.py                      # Конфиг PostgreSQL
```

---

## Описание модулей

### Детекторы (`detectors/`)

Определяют текущее состояние страницы Avito.

| Файл | ID детектора | Назначение |
|------|--------------|------------|
| `detect_page_state.py` | — | Главный роутер: вызывает детекторы по приоритету, возвращает ID первого сработавшего. При неуспехе — 3 повторные попытки с интервалом 20 сек |
| `catalog_page_detector.py` | `catalog_page_detector` | Ищет `div[data-marker="catalog-serp"]` и карточки каталога |
| `card_found_detector.py` | `card_found_detector` | Проверяет `span[data-marker="item-view/item-id"]` |
| `seller_profile_detector.py` | `seller_profile_detector` | Ищет маркеры профиля продавца |
| `captcha_geetest_detector.py` | `captcha_geetest_detector` | Детектирует Geetest-капчу (опрос до 3 сек) |
| `continue_button_detector.py` | `continue_button_detector` | Ищет `button[name="submit"]` |
| `proxy_block_403_detector.py` | `proxy_block_403_detector` | HTTP 403 или фразы блокировки IP |
| `proxy_block_429_detector.py` | `proxy_block_429_detector` | HTTP 429 (rate limiting) |
| `proxy_auth_407_detector.py` | `proxy_auth_407_detector` | HTTP 407 (требуется авторизация прокси) |
| `removed_or_not_found_detector.py` | `removed_or_not_found_detector` | HTTP 404/410 или маркеры удалённого объявления |

**Порядок приоритета по умолчанию:**
1. proxy_block_403_detector
2. proxy_block_429_detector
3. proxy_auth_407_detector
4. captcha_geetest_detector
5. removed_or_not_found_detector
6. seller_profile_detector
7. catalog_page_detector
8. card_found_detector
9. continue_button_detector

---

### Парсеры (`parsers/`)

#### Card Parser (`card_parser.py`)

Парсит HTML отдельной карточки объявления.

```python
parse_card(html, *, fields, ensure_card=True, include_html=False) -> CardData
```

**CardData поля:** `title`, `price`, `seller`, `item_id`, `published_at`, `description`, `location`, `characteristics`, `views_total`, `images`, `raw_html`

#### Catalog Parser (`catalog_parser/`)

Парсит страницы каталога с пагинацией.

```python
parse_catalog(page, catalog_url, *, fields, max_pages=1, sort_by_date=False, ...) -> (list[CatalogListing], CatalogParseMeta)
```

**Компоненты:**
- `catalog_parser.py` — основная логика парсинга
- `models.py` — `CatalogListing`, `CatalogParseMeta`, `CatalogParseStatus`
- `helpers.py` — скроллинг, извлечение карточек, пагинация
- `steam.py` — оркестратор `parse_catalog_until_complete` с повторными попытками

**CatalogListing поля:** `item_id`, `title`, `price`, `snippet_text`, `location_city`, `location_area`, `seller_name`, `seller_id`, `seller_rating`, `seller_reviews`, `promoted`, `published_ago`, `raw_html`

#### Seller Profile Parser (`seller_profile_parser.py`)

Собирает данные о продавце через API.

```python
collect_seller_items(page, *, min_price=8000, condition_titles=None, ...) -> SellerProfileParsingResult
```

**Логика:** детектирует профиль → решает капчу → извлекает seller_id → вызывает API `/web/1/profile/items`

---

### Капча (`capcha/`)

Решение Geetest-капчи со слайдером.

| Файл | Назначение |
|------|------------|
| `resolver.py` | Оркестратор `resolve_captcha_flow(page, max_attempts=30)` — возвращает `(html, solved: bool)` |
| `solve_slider_once.py` | Одна попытка решения: загрузка картинок → поиск в кеше → OpenCV matching → drag&drop |
| `solver_utils.py` | `calculate_hash()` — SHA512 ключ; `calculate_offset()` — OpenCV template matching |
| `cache_manager.py` | `get_offset()`, `update_offset()`, `record_failure()` |
| `cache_io.py` | Чтение/запись кеша (JSON или PostgreSQL) |

**Алгоритм решения:**
1. Извлечь URL фона и пазла из CSS
2. Вычислить хеш (SHA512)
3. Найти в кеше или вычислить смещение через OpenCV
4. Выполнить drag&drop на слайдере
5. Обновить кеш с результатом

---

### Утилиты (`utils/`)

#### Continue Button (`continue_button.py`)

```python
press_continue_and_detect(page, *, skip_initial_detector=False, max_retries=10, ...) -> str
```

Нажимает кнопку "Продолжить" и определяет новое состояние страницы.

---

### Переиспользуемые компоненты (`reuse_utils/`)

#### Task Queue (`task_queue.py`)

FIFO-очередь задач для парсинга.

```python
class TaskQueue:
    put_many(items)          # Добавить задачи
    get() -> ProcessingTask  # Получить следующую
    mark_done(task_key)      # Отметить выполненной
    retry(task_key, reason)  # Вернуть в очередь
    pause() / resume()       # Пауза/возобновление
```

**Особенности:** уникальные ключи, учёт попыток, asyncio-совместимость

#### Proxy Pool (`proxy_pool.py`)

Кольцевой пул прокси с blacklist.

```python
class ProxyPool:
    create(proxies_file, blocked_file) -> ProxyPool
    acquire() -> ProxyEndpoint  # Round-robin выдача
    release(address)            # Освобождение
    block(address, persistent)  # Добавление в blacklist
    reload()                    # Перечитать файлы
```

---

### Отладка (`debug/`)

#### Screenshot (`screenshot.py`)

```python
capture_debug_screenshot(page, *, enabled=None, label=None, ...) -> Optional[Path]
```

Сохраняет скриншоты для отладки. Контролируется env `AVITO_DEBUG_SCREENSHOTS`.

---

### Конфигурация

#### `config.py`

```python
MAX_PAGE: int | None = None  # Глобальный лимит страниц
```

#### `data/db_data.py`

Конфигурация хранилища кеша капчи:
- `STORAGE_MODE` — "json" или "postgres"
- `POSTGRES_TABLE_NAME` — имя таблицы
- `POSTGRES_OFFSET_COLUMN` — имя колонки смещения

---

## Главный API

Импорты через `avito_library`:

```python
from avito_library import (
    # Детекторы
    detect_page_state,
    DetectionError,
    NOT_DETECTED_STATE_ID,
    DETECTOR_FUNCTIONS,
    DETECTOR_DEFAULT_ORDER,

    # Константы детекторов
    CAPTCHA_DETECTOR_ID,
    CARD_FOUND_DETECTOR_ID,
    CATALOG_DETECTOR_ID,
    CONTINUE_BUTTON_DETECTOR_ID,
    PROXY_AUTH_DETECTOR_ID,
    PROXY_BLOCK_403_DETECTOR_ID,
    PROXY_BLOCK_429_DETECTOR_ID,
    REMOVED_DETECTOR_ID,
    SELLER_PROFILE_DETECTOR_ID,

    # Парсеры
    parse_card, CardData, CardParsingError,
    parse_catalog, CatalogListing, CatalogParseMeta, CatalogParseResult, CatalogParseStatus,
    collect_seller_items, SellerProfileParsingResult, SellerIdNotFound,

    # Капча
    resolve_captcha_flow,
    solve_slider_once,

    # Утилиты
    press_continue_and_detect,
    install_playwright_chromium,
    MAX_PAGE,
)
```

---

## Архитектура

```
┌─────────────────────────────────────────────────────────────────┐
│                      Главный API (avito_library)                │
│  detect_page_state | parse_card | parse_catalog |               │
│  collect_seller_items | resolve_captcha_flow | press_continue   │
└─────────────────────────────────────────────────────────────────┘
          │              │              │              │
          ▼              ▼              ▼              ▼
    ┌─────────┐   ┌──────────┐   ┌──────────┐   ┌─────────┐
    │Detectors│   │  Parsers │   │  Capcha  │   │ Utils   │
    │         │   │          │   │          │   │         │
    │ 9 типов │   │ Card     │   │ Resolver │   │ Continue│
    │ + Router│   │ Catalog  │   │ Solver   │   │ Button  │
    │         │   │ Seller   │   │ Cache    │   │         │
    └─────────┘   └──────────┘   └──────────┘   └─────────┘
          │              │              │              │
          ▼              ▼              ▼              ▼
    ┌─────────────────────────────────────────────────────┐
    │         Переиспользуемые утилиты (reuse_utils)      │
    │            TaskQueue  |  ProxyPool                  │
    └─────────────────────────────────────────────────────┘
          │              │
          ▼              ▼
    ┌──────────────────────────────────┐
    │  Playwright  |  OpenCV  |  BS4   │
    │  PostgreSQL  |  asyncpg          │
    └──────────────────────────────────┘
```

---

## Потоки данных

### Парсинг каталога

```
parse_catalog()
├── press_continue_and_detect()
│   ├── detect_page_state()
│   └── click button
├── resolve_captcha_flow() [при капче]
│   ├── press_continue_and_detect()
│   └── solve_slider_once()
│       ├── calculate_hash()
│       ├── get_offset() / calculate_offset()
│       └── update_offset()
└── load_catalog_cards()
    └── extract_listing()
```

### Сбор данных продавца

```
collect_seller_items()
├── detect_page_state()
├── resolve_captcha_flow() [при капче]
└── fetch_api("/web/1/profile/items")
```

---

## Переменные окружения

| Переменная | Значение | Назначение |
|------------|----------|------------|
| `AVITO_DEBUG_SCREENSHOTS` | int (0/1/...) | Включить сохранение скриншотов |
| `AVITO_DEBUG_SCREENSHOT_TIMEOUT_MS` | int | Таймаут для скриншота (default: 5000) |

---

## CLI команды

```bash
# Установка Chromium для Playwright
avito-install-chromium
```

---

## Пример использования

```python
import asyncio
from playwright.async_api import async_playwright
from avito_library import (
    detect_page_state,
    parse_catalog,
    resolve_captcha_flow,
    CAPTCHA_DETECTOR_ID,
)

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()

        response = await page.goto("https://avito.ru/moskva/telefony")
        state = await detect_page_state(page, last_response=response)

        if state == CAPTCHA_DETECTOR_ID:
            html, solved = await resolve_captcha_flow(page)
            if not solved:
                print("Капча не решена")
                return

        listings, meta = await parse_catalog(
            page,
            "https://avito.ru/moskva/telefony",
            fields=["item_id", "title", "price"],
            max_pages=3,
        )

        for listing in listings:
            print(f"{listing.item_id}: {listing.title} - {listing.price} руб.")

        await browser.close()

asyncio.run(main())
```
