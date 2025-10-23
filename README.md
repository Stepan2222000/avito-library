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
avito-install-chromium  # или playwright install chromium
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

### Детекторы (`avito_library.detectors`)
- `detect_page_state(page: Page, ...) -> str`: запускает зарегистрированные детекторы в порядке приоритета и возвращает идентификатор состояния.
- `DetectionError`: исключение, выбрасывается, если состояние определить не удалось.
- Константы `*_DETECTOR_ID`: строковые идентификаторы, которые возвращают отдельные детекторы.
- `DETECTOR_FUNCTIONS`, `DETECTOR_DEFAULT_ORDER`, `DETECTOR_WAIT_TIMEOUT_RESOLVERS`: словари/кортежи для ручного конфигурирования внешними системами.

### Утилиты (`avito_library.utils`)
- `press_continue_and_detect(page: Page, ...) -> str`: нажимает кнопку «Продолжить» (если она есть) и определяет новое состояние страницы с учётом капчи и блокировок.

### Капча (`avito_library.capcha`)
- `resolve_captcha_flow(page: Page, max_attempts: int = 3) -> tuple[str, bool]`: пытается пройти геест-капчу, возвращает HTML и признак успеха.
- `solve_slider_once(page: Page) -> tuple[str, bool]`: один прогон решения с расчётом смещения с помощью OpenCV.

### Парсер карточек (`avito_library.parsers.card_parser`)
- `parse_card(html: str, fields: Iterable[str], ensure_card: bool = True, include_html: bool = False) -> CardData`: извлекает указанные поля из HTML карточки.
- `CardData`: dataclass с полями объявления (заголовок, цена, продавец, id и т. п.).
- `CardParsingError`: исключение, если HTML не похож на карточку.

### Парсер каталога (`avito_library.parsers.catalog_parser`)
- `parse_catalog(page: Page, catalog_url: str, fields: Iterable[str], ...) -> CatalogParseResult`: обходит каталог, управляет повторными запросами и детекторами состояний.
- `CatalogListing`: dataclass карточки каталога.
- `CatalogParseMeta`: метаинформация о выполнении (обработанные страницы, состояние и т. п.).
- `CatalogParseStatus`: перечисление возможных статусов (`success`, `rate_limit`, `captcha_unsolved` и др.).
- `parse_catalog_until_complete(...) -> CatalogParseResult`: оркестратор, который повторно запрашивает страницы через `wait_for_page_request` / `supply_page`.
- `PageRequest`: структура запроса новой страницы от внешнего координатора.

### Парсер продавца (`avito_library.parsers.seller_profile_parser`)
- `collect_seller_items(page: Page, ...) -> dict`: собирает информацию о продавце и его объявлениях, переиспользуя текущую страницу.
- `SellerProfileParsingResult`: тип результата (словарь с ключами `seller_name`, `item_ids`, `state`, `is_complete`).
- `SellerIdNotFound`: исключение, если идентификатор продавца не найден.

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
