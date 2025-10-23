# Парсер каталога Авито

Документ описывает API и внутреннюю логику асинхронного парсера каталогов Авито. Модуль предназначен для загрузки страниц листинга, обхода пагинации, опциональной сортировки «по дате» и извлечения указанного набора полей из карточек объявлений.

## Сигнатура

```python
@dataclass(slots=True)
class CatalogListing:
    item_id: str  # идентификатор объявления (из data-item-id или строки вида “№ 123…”)
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
    raw_html: str | None  # включается по флагу include_html


async def parse_catalog(
    page: Page,
    catalog_url: str,
    *,
    fields: Iterable[str],
    max_pages: int | None = 1,
    sort_by_date: bool = False,
    include_html: bool = False,
    start_page: int = 1,
) -> list[CatalogListing]:
    ...
```

- `page` — экземпляр `playwright.async_api.Page`.
- `catalog_url` — ссылка на первую страницу каталога.
- `fields` — перечисление идентификаторов полей, которые необходимо заполнить (см. таблицу ниже). Незапрошенные поля остаются `None` и не требуют дополнительных селекторов.
- `max_pages` — максимальное количество страниц для обхода (минимум 1) или `None` для полного обхода.
- `sort_by_date` — при `True` парсер добавляет к URL параметр `s=104` (стандартный сортировочный ключ Авито «По дате»).
- `include_html` — если `True`, в `CatalogListing.raw_html` сохраняется `inner_html` карточки.
- `start_page` — порядковый номер страницы, с которой следует начать обход (по умолчанию 1).

## Поток выполнения

1. **Подготовка URL.** Если `sort_by_date=True`, к `catalog_url` добавляется или заменяется query-параметр `s=104`. Остальные параметры сохраняются.
2. **Загрузка страницы.** Выполняется `page.goto` (режим `wait_until="domcontentloaded"`, таймаут 60 секунд).
3. **Обработка переходов.** Для каждого захода на страницу каталога:
   - вызывается `press_continue_and_detect(page)`; если возвращается состояние `captcha_geetest_detector` или прочие детекторы, которые относятся к капче, используется кооператор `resolve_captcha_flow` до получения разрешённого состояния;
   - состояние подтверждается через `detect_page_state`. При ответе состоянием, которое не является капчей и не является валидным для дальнейшей работы - парсер прекращает работу и возвращает накопленный результат.
4. **Ленивая загрузка карточек.** Выполняется цикл «скролл → ожидание → подсчёт»:
   - `page.evaluate("window.scrollTo(0, document.body.scrollHeight)")`;
   - `page.wait_for_load_state("networkidle")` (fallback — `wait_for_timeout(2000)`);
   - карточки собираются по селектору `div[data-marker="item"]`. Если число стабилизировалось или достигнут лимит попыток (10), скроллинг завершается.
5. **Извлечение данных.** Для каждой карточки вызывается `extract_listing(card_locator, fields)`, который возвращает заполненный `CatalogListing`. Поддерживаемые поля описаны ниже.
6. **Пагинация.** Если найден `a[data-marker="pagination-button/nextPage"]` и не достигнут лимит страниц, парсер строит абсолютный URL следующей страницы, выполняет `page.goto` и повторяет шаги 3–5. Иначе цикл завершается.
7. **Возврат.** Функция возвращает список `CatalogListing` в порядке обхода.

## Поля карточки и источники

| Поле | Идентификатор `fields` | Источник / правила | Ограничения |
|------|------------------------|--------------------|-------------|
| `item_id` | всегда | Основное значение берётся из `div[data-marker="item"]` → `data-item-id`. Если атрибут отсутствует, используется текст строки `div[data-marker="item-line"]` (например, `№ 1234567890`). | Всегда строка. |
| `title` | `title` | `a[data-marker="item-title"]` → `.get_text(strip=True)`. | |
| `price` | `price` | `span[data-marker="item-price"]`. Из строки извлекается числовое значение (учитываем неразрывные пробелы). Если в тексте «от … ₽», берём минимальную цифру. | |
| `snippet_text` | `snippet` | `meta[itemprop="description"]` + ближайший `p` в описании (предпросмотр). | |
| `location_city` / `location_area` / `location_extra` | `location` | `span` с иконкой `geo-pinIcon-*`. Текст вида «Москва, Румянцево» → первая часть — город, вторая — район/метро. Дополнительные хвосты («21–30 мин.») пишем в `location_extra`. | |
| `published_ago` | `published` | `span[data-marker="item-date"]` (например, «3 часа назад»). | |
| `seller_name` | `seller_name` | `a[href*="/brands/"]` или `a[href*="/user/"]` → вложенный `p`. Если ссылки нет, ищем правую колонку с именем (`div.iva-item-sellerInfo`). | |
| `seller_id` | `seller_id` | Из соответствующего `href` вырезаем идентификатор: `/brands/i1232399992...?` → `i1232399992`; `/user/7669122005...?` → `7669122005`. | Может отсутствовать при отсутствии ссылки. |
| `seller_rating` | `seller_rating` | `span[data-marker="seller-info/score"]` → `float`. | |
| `seller_reviews` | `seller_reviews` | `p[data-marker="seller-info/summary"]` → извлекаем число из строки «45 отзывов». | |
| `promoted` | `promoted` | Наличие `span` с текстом `Продвинуто` (`span.styles-module-noAccent-nSgNq`). Флаг `True/False`. | |
| `raw_html` | `raw_html` | `card_locator.inner_html()` при `include_html=True`. | |

> Значки услуг («Собственник», «Можно в кредит»), а также медиагалерея не собираются на этом этапе.

## Псевдокод извлечения карточки

```python
async def extract_listing(card: Locator, fields: set[str], *, include_html: bool) -> CatalogListing:
    listing = CatalogListing(
        item_id=await card.get_attribute("data-item-id") or "",
        title=None,
        price=None,
        snippet_text=None,
        location_city=None,
        location_area=None,
        location_extra=None,
        seller_name=None,
        seller_id=None,
        seller_rating=None,
        seller_reviews=None,
        promoted=False,
        published_ago=None,
        raw_html=None,
    )

    link = card.locator('a[data-marker="item-title"]').first
    if await link.count() and "title" in fields:
        listing.title = (await link.inner_text()).strip()

    if listing.item_id == "":
        node = card.locator('div[data-marker="item-line"]').first
        if await node.count():
            listing.item_id = (await node.inner_text()).strip()

    if "price" in fields:
        price_node = card.locator('span[data-marker="item-price"]').first
        if await price_node.count():
            price_text = (await price_node.inner_text()).strip()
            listing.price = parse_price(price_text)

    if "snippet" in fields:
        listing.snippet_text = await extract_snippet(card)

    if "location" in fields:
        listing.location_city, listing.location_area, listing.location_extra = await extract_location(card)

    if "published" in fields:
        node = card.locator('span[data-marker="item-date"]').first
        if await node.count():
            listing.published_ago = (await node.inner_text()).strip()

    if {"seller_name", "seller_id", "seller_rating", "seller_reviews"} & fields:
        await fill_seller_info(card, listing, fields)

    if "promoted" in fields:
        listing.promoted = await card.locator('span:has-text("Продвинуто")').count() > 0

    if include_html:
        listing.raw_html = await card.inner_html()

    return listing
```

Вспомогательные функции `extract_snippet`, `extract_location`, `fill_seller_info`, `parse_price` документируются в коде и используют только необходимые селекторы.

## Особенности реализации

- `fields` должен быть приведён к `set[str]` в начале функции, чтобы проверки были `O(1)`.
- Если `max_pages` меньше фактического числа страниц, парсер прекращает работу после чтения указанного количества.
- При переходе на следующую страницу `page.goto` выполняется через абсолютный URL (`https://www.avito.ru` + `href`).
- При отсутствии карточек после нескольких скроллов проверяем HTML: если найдены фразы «ничего не найдено» или «Доступ ограничен», возвращаем текущий список без ошибок.
- Парсер не обрабатывает пользовательские паузы, заголовки и прокси — этим управляет вызывающий код.
- Для корректной работы с цифрами и пробелами нужно заменять неразрывные пробелы (`\u00a0`) на обычные пробелы перед конвертацией.

## Пример использования

```python
fields = {"title", "price", "seller_name", "seller_id", "seller_rating", "seller_reviews", "promoted", "published"}
listings = await parse_catalog(
    page,
    "https://www.avito.ru/moskva/avtomobili/audi-ASgBAgICAUTgtg3QmCg",
    fields=fields,
    max_pages=2,
    sort_by_date=True,
    start_page=1,
)

for item in listings:
    print(item.item_id, item.title, item.price, item.seller_name, item.promoted)
```

## Полный проход каталога

Используйте `parse_catalog_until_complete` из `stream.py`, чтобы повторно запускать парсинг при временных блокировках.
Функция публикует запросы на новую страницу через `wait_for_page_request`, а внешняя логика должна отвечать методом
`supply_page`. После каждого полученного `SUCCESS` возвращаются полные данные, иначе — частичный результат с пометкой.

Документ служит эталоном для разработки и тестов. Реализация должна следовать указанной структуре, но допускает вспомогательные функции и классы для организации кода.

## План реализации

- Подготовка входных данных: привести `fields` к `set[str]`, гарантировать наличие `item_id`, проверить `max_pages >= 1`, разобрать `catalog_url` и при необходимости добавить/заменить `s=104`.
- Главный цикл по страницам (до `max_pages`): перед входом сохранять текущий URL и номер шага, чтобы при раннем выходе корректно вернуть накопленные результаты.
- Навигация: выполнять `page.goto` с `wait_until="domcontentloaded"` и общим таймаутом 60 секунд; после перехода сразу вызывать `press_continue_and_detect(page)` и `detect_page_state(page)` из `detectors/detect_page_state.py`.
- Обработка состояний: при капче запускать `resolve_captcha_flow` из `capcha/resolver.py` до получения разрешённого состояния; при 403/407 инициировать логику смены прокси в вызывающем коде (возвращаем результат); при иных недопустимых состояниях завершать парсинг с текущими данными.
- Ленивая загрузка карточек: реализовать вспомогательную корутину `load_catalog_cards`, которая в цикле до 10 итераций вызывает `scrollTo`, `wait_for_load_state("networkidle")` или `wait_for_timeout`, и проверяет стабильность количества элементов `div[data-marker="item"]`.
- Извлечение карточек: получать локаторы карточек, при отсутствии — повторно проверять HTML на ключевые фразы и по необходимости завершать работу без ошибок; для каждой карточки вызывать `extract_listing(card, fields, include_html)` с точечным доступом к селекторам.
- Обработка полей: внутри `extract_listing` использовать отдельные функции (`extract_snippet`, `extract_location`, `fill_seller_info`, `parse_price`) и нормализовать текст (в том числе замену `\u00a0`), избегая лишних обращений к DOM для незапрошенных полей.
- Пагинация: искать `a[data-marker="pagination-button/nextPage"]`, формировать абсолютный URL через `urllib.parse.urljoin`, инкрементировать счетчик страниц и переходить на следующую страницу; при отсутствии ссылки завершать цикл.
- Возврат: поддерживать список `CatalogListing` в порядке обхода, возвращать его при успешном завершении или любом раннем выходе; предусмотреть логирование ключевых этапов (уровень DEBUG) без излишних зависимостей.
