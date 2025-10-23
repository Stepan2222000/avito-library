# avito-library

Playwright-библиотека для асинхронного парсинга Авито. Пакет объединяет детекторы состояний страницы, утилиту нажатия «Продолжить», решатель Geetest-капчи и парсеры каталога, карточек и профилей продавцов. Всё взаимодействие с сайтом выполняется через Playwright — дополнительных HTTP-клиентов не требуется.

## Возможности

- **Детекторы состояний**: определяют, на какой странице оказался браузер (карточка, каталог, капча, блокировки прокси и т. д.) и выдают стабильные идентификаторы состояний.
- **Утилита `press_continue_and_detect`**: переиспользует долговечную страницу Playwright, жмёт кнопку «Продолжить» и повторно определяет состояние.
- **Решатель Geetest**: `resolve_captcha_flow` и `solve_slider_once` реализуют попытку решения геест-капчи с кешированием смещений и обработкой повторов.
- **Парсеры**:
  - `parse_card` — разбирает HTML карточки в структуру `CardData`.
  - `parse_catalog` и `parse_catalog_until_complete` — итерируют каталог с обработкой капчи/блокировок и возвращают список `CatalogListing` + метаданные.
  - `collect_seller_items` — собирает информацию о продавце и его объявлениях, повторно используя текущую страницу.

## Системные требования

- Python 3.13+
- Chromium, устанавливаемый через Playwright (`playwright install chromium`)
- OS с поддержкой Playwright Chromium (Linux, macOS, Windows)

## Установка

```bash
pip install git+https://github.com/Stepan2222000/avito-library.git@v0.1.0#egg=avito-library
playwright install chromium  # выполнить один раз после установки
```

Для использования внутри `requirements.txt` добавьте строку:

```
git+https://github.com/Stepan2222000/avito-library.git@v0.1.0#egg=avito-library
```

При обновлении библиотеки достаточно выпустить новый тег (например, `v0.1.1`) и изменить ссылку в зависимых проектах.

## Быстрый старт

```python
import asyncio
from playwright.async_api import async_playwright

from avito_library import (
    parse_catalog,
    collect_seller_items,
    detect_page_state,
    press_continue_and_detect,
    resolve_captcha_flow,
)

CATALOG_FIELDS = {
    "item_id",
    "title",
    "price",
    "seller_name",
}

async def main() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Переход на каталог и первичное определение состояния.
        await page.goto("https://www.avito.ru/moskva/avtomobili", wait_until="domcontentloaded")
        state = await detect_page_state(page)
        if state == "captcha_geetest_detector":
            await resolve_captcha_flow(page)
            state = await detect_page_state(page)

        if state == "catalog_page_detector":
            listings, meta = await parse_catalog(
                page,
                "https://www.avito.ru/moskva/avtomobili",
                fields=CATALOG_FIELDS,
                max_pages=1,
                include_html=False,
            )
            print(f"Получено {len(listings)} объявлений, статус: {meta.status}")

        seller_result = await collect_seller_items(page)
        print(f"Продавец: {seller_result['seller_name']}")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
```

## API-справочник

### Базовый модуль `avito_library`
- `MAX_PAGE: int | None` — глобальный предел страниц, который учитывает `collect_seller_items`. Установите его в целевом проекте, чтобы ограничить глубину пагинации профиля.
- `install_playwright_chromium(check: bool = True) -> int` — обёртка над `python -m playwright install chromium`. Возвращает код выхода Playwright и позволяет управлять флагом `check` при запуске в CI/CD.
- `install_playwright_chromium_cli()` — CLI-энтрипоинт (экспортируется как консольный скрипт `avito-install-chromium`).

### Детекторы (`avito_library.detectors`)
- `detect_page_state(page: Page, *, skip=None, priority=None, detector_kwargs=None, last_response=None) -> str`  
  Выполняет зарегистрированные детекторы в порядке приоритета и возвращает идентификатор состояния.  
  Параметры:
  - `skip`: Iterable[str] — список детекторов, которые следует исключить (например, `{"captcha_geetest_detector"}`).
  - `priority`: Sequence[str] — собственный порядок обхода. Переданные идентификаторы будут проверены раньше дефолтного порядка.
  - `detector_kwargs`: Mapping[str, Mapping[str, object]] — дополнительные аргументы для отдельных детекторов (например, таймауты капчи).
  - `last_response`: Response | None — последний HTTP-ответ Playwright; нужен детекторам, которые анализируют статус-коды.
  Возвращает строковый идентификатор детектора (например, `catalog_page_detector`). Выбрасывает `DetectionError`, если ни один детектор не сработал или переданы неизвестные идентификаторы.
- `DetectionError` — лучше ловить вокруг вызовов навигации и логировать пустой HTML, чтобы диагностировать новые состояния.
- Регистры:
  - `DETECTOR_FUNCTIONS` — словарь `id -> coroutine`.
  - `DETECTOR_DEFAULT_ORDER` — последовательность, описывающая стандартный приоритет (блокировки → капча → каталог → карточка → continue).
  - `DETECTOR_WAIT_TIMEOUT_RESOLVERS` — позволяет централизованно вычислять таймауты для отдельных детекторов (сейчас используется только капча).
- Идентификаторы состояний:
  - `CAPTCHA_DETECTOR_ID` — видим Geetest.
  - `CONTINUE_BUTTON_DETECTOR_ID` — отображается кнопка «Продолжить».
  - `CATALOG_DETECTOR_ID` — мы в каталоге.
  - `CARD_FOUND_DETECTOR_ID` — карточка объявления.
  - `SELLER_PROFILE_DETECTOR_ID` — профиль продавца.
  - `PROXY_BLOCK_403_DETECTOR_ID` / `PROXY_BLOCK_429_DETECTOR_ID` / `PROXY_AUTH_DETECTOR_ID` — разные варианты блокировок прокси.
  - `REMOVED_DETECTOR_ID` — объявление снято или удалено.

### Утилита «Продолжить» (`avito_library.utils.press_continue_and_detect`)
- `press_continue_and_detect(page: Page, *, skip_initial_detector=False, detector_kwargs=None, max_retries=10, wait_timeout=30.0) -> str`  
  Имитация ручного нажатия кнопки «Продолжить» с повторным детектом состояния.  
  Логика:
  1. По умолчанию сначала вызывается `detect_page_state` с собственным приоритетом, чтобы избежать лишних кликов.
  2. Если требуется, нажимает кнопку до пяти раз подряд (force-click) и ждёт изменения состояния.
  3. Каждые 10 секунд проверяет состояние, пока не выйдет `wait_timeout`.  
  Возвращает итоговый идентификатор состояния (капча, каталог, карточка и т. д.). Полезно вызывать перед парсингом или после редиректов.

### Геест-капча (`avito_library.capcha`)
- `resolve_captcha_flow(page: Page, *, max_attempts: int = 3) -> tuple[str, bool]`  
  Комплектует нажатие «Продолжить», одноразовый солвер и повторную проверку состояния. Возвращает последний HTML и флаг `solved`. Если капча не исчезает или приходит 429, вернётся `False`. Используйте при ответе `detect_page_state` равном `CAPTCHA_DETECTOR_ID` или `PROXY_BLOCK_429_DETECTOR_ID`.
- `solve_slider_once(page: Page) -> tuple[str, bool]`  
  Выполняет один прогон Geetest: тянет изображения через Playwright, вычисляет смещение с помощью OpenCV, использует кеш (`data/geetest_cache.json`). Возвращает HTML и признак успеха. Рекомендуется вызывать напрямую только для отладки; в проде используйте `resolve_captcha_flow`.

### Парсер карточек (`avito_library.parsers.card_parser`)
- `parse_card(html: str, *, fields: Iterable[str], ensure_card: bool = True, include_html: bool = False) -> CardData`  
  Извлекает указанные поля (см. ниже) из HTML карточки.  
  Особенности:
  - `fields` — набор строк, допустимые значения: `title`, `price`, `seller`, `item_id`, `published_at`, `description`, `location`, `characteristics`, `views_total`, `raw_html`.
  - `ensure_card=True` заставляет проверять наличие идентификатора карточки и выбрасывать `CardParsingError`, если HTML не похож на карточку.
  - `include_html=True` независимо от `fields` кладёт исходный HTML в `CardData.raw_html`.
- `CardData` — dataclass с полями объявления. Все значения опциональны, чтобы устойчиво переживать неполные данные.
- `CardParsingError` — бросается при отсутствии обязательной разметки. Ловите его, если HTML пришёл с ошибкой.

### Парсер каталога (`avito_library.parsers.catalog_parser`)
- `parse_catalog(page: Page, catalog_url: str, *, fields: Iterable[str], max_pages: int | None = 1, sort_by_date: bool = False, include_html: bool = False, start_page: int = 1) -> CatalogParseResult`  
  Загружает страницы каталога, кликает «Продолжить», решает капчу и собирает карточки.  
  Советы по использованию:
  - Передавайте `fields` с подмножеством ключей: `title`, `price`, `seller_name`, `seller_id`, `seller_rating`, `seller_reviews`, `snippet`, `location`, `promoted`, `published`, `raw_html`.
  - `max_pages=None` включает полный обход, иначе ограничивает количество страниц (вместе с `start_page`).
  - `sort_by_date=True` добавляет `s=104` к URL.
  - Возвращаемое значение — `(listings, meta)`, где `listings` — список `CatalogListing`, `meta` — `CatalogParseMeta`.
  - При получении статуса `CatalogParseStatus.CAPTCHA_UNSOLVED` имеет смысл вызвать `resolve_captcha_flow` и повторить запрос.
- `CatalogListing` — модель карточки каталога (ID, заголовок, цена, продавец, промометки, HTML).
- `CatalogParseMeta` — содержит статус, количество обработанных страниц/карточек, последний URL и текстовые детали.
- `CatalogParseStatus` — перечисление возможных исходов (`SUCCESS`, `EMPTY`, `RATE_LIMIT`, `PROXY_BLOCKED` и т. д.).
- Потоковый режим:
  - `parse_catalog_until_complete(...) -> CatalogParseResult` — выполняет многошаговый обход, автоматически дозапрашивая свежие страницы до успеха или исчерпания лимита.
  - `PageRequest` — объект, который оркестратор отправляет внешний системе, если нужна новая страница Playwright.
  - `wait_for_page_request()`, `supply_page(page)`, `set_page_exchange(exchange)` — вспомогательные функции для интеграции с менеджером браузерных страниц. Используйте их, если вы управляете пулом Playwright-страниц вручную.

### Парсер профиля продавца (`avito_library.parsers.seller_profile_parser`)
- `collect_seller_items(page: Page, *, min_price: int | None = 8000, condition_titles: Sequence[str] | None = None) -> SellerProfileParsingResult`  
  Снимает имя продавца и список ID его объявлений, используя API `/web/1/profile/items`.  
  Поведение:
  - Перед началом вызывает `detect_page_state`; при капче — `resolve_captcha_flow`.
  - `min_price` фильтрует объявления по минимальной цене (значение извлекается из JSON).
  - `condition_titles` — список значений бейджей (например, `["Новый", "Как новый"]`); приводятся к нижнему регистру.
  - Возвращает словарь с ключами: `state`, `seller_name`, `item_ids`, `pages_collected`, `is_complete`. В `state` остаётся идентификатор детектора, который завершил работу (чаще всего `seller_profile_detector`).
- `SellerProfileParsingResult` — псевдоним словаря результата (см. выше).
- `SellerIdNotFound` — исключение, выбрасываемое, если парсер не нашёл `sellerId` в HTML (перехватывается внутри `collect_seller_items`, но полезно для тестов).


## Данные

Файл `data/geetest_cache.json` используется для кеширования смещений при решении капчи. Он автоматически обновляется во время работы библиотеки и включён в пакет.

## Разработка и проверка

```bash
python -m venv .venv
source .venv/bin/activate  # или .venv\Scripts\activate на Windows
pip install -e .
playwright install chromium
python - <<'PY'
import avito_library
print(avito_library.detect_page_state)
PY
```

## Публикация

1. Инициализируйте Git и привяжите удалённый репозиторий `git remote add origin git@github.com:<org>/avito-library.git`.
2. Закоммитьте содержимое `git add . && git commit -m "Initial release"`.
3. Запушьте `git push -u origin main` (или `master`).
4. Создайте тег релиза `git tag v0.1.0 && git push origin v0.1.0`.

После этого библиотеку можно подключать из любого проекта или Docker-контейнера одной строкой в requirements.
