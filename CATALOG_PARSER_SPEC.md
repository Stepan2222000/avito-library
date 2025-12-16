# Спецификация: Парсер каталога Avito (v2)

## Суть идеи

Парсер каталога работает по принципу "продолжаемой операции". Внешний код открывает страницу каталога через `navigate_to_catalog()`, вызывает парсер, получает результат. Если произошла критическая ошибка (проблема с прокси) — парсер возвращает частичные данные и информацию для продолжения. Внешний код создаёт новую страницу с новым прокси и продолжает парсинг с того места, где остановились.

---

## Ключевые принципы

1. **Страница уже открыта** — при вызове парсера страница уже находится на каталоге, парсер не делает первый goto.

2. **Навигация через `navigate_to_catalog`** — для перехода на страницы каталога используется функция-обёртка над `page.goto()`, которая применяет параметры sort и start_page к URL.

3. **Три уровня API**:
   - `navigate_to_catalog` — переход на страницу каталога с параметрами
   - `parse_single_page` — парсинг одной страницы (страница уже открыта)
   - `parse_catalog` — парсинг всех страниц с пагинацией

4. **Накопление данных** — карточки собираются страница за страницей и хранятся в объекте результата.

5. **Критические ошибки возвращают управление** — при блокировке прокси (403/407), таймауте или нераспознанном состоянии парсер останавливается и возвращает то, что успел собрать, плюс информацию для продолжения.

6. **Некритические ошибки решаются внутри** — капча, кнопка "Продолжить", rate-limit (429) обрабатываются внутри парсера без участия внешнего кода.

7. **Продолжение через метод** — объект результата имеет метод `continue_from(new_page)`, который принимает новую страницу и продолжает парсинг.

8. **Нет глобального состояния** — всё состояние хранится в объекте результата, можно запускать несколько парсеров параллельно.

---

## Функция navigate_to_catalog

Обёртка над `page.goto()` для перехода на страницу каталога с параметрами.

**Что делает:**
1. Принимает базовый URL каталога и параметры (sort, start_page)
2. Формирует финальный URL с query-параметрами (`?s=104&p=3`)
3. Выполняет `page.goto()` с указанными настройками
4. Возвращает `Response` (как обычный goto)

**Что НЕ делает:**
- Не решает капчу
- Не детектирует состояние страницы
- Не парсит карточки

**Сигнатура:**
```
navigate_to_catalog(
    page: Page,
    catalog_url: str,
    *,
    sort: str | None = None,      # date, price_asc, price_desc, mileage_asc
    start_page: int = 1,
    timeout: int = 180_000,       # 3 минуты
    wait_until: str = "domcontentloaded",
) -> Response
```

**Что возвращает:**
- `Response` — объект ответа Playwright (то же, что `page.goto()`)

**Использование:**
```python
from avito_library import navigate_to_catalog

# Переход на каталог с сортировкой по дате, страница 3
response = await navigate_to_catalog(
    page,
    "https://avito.ru/moskva/telefony",
    sort="date",
    start_page=3,
)
```

---

## Функции парсинга

### parse_single_page — парсинг одной страницы

Парсит одну страницу каталога. Страница уже должна быть открыта.

**Что делает:**
1. Вызывает `detect_page_state()` для определения состояния страницы
2. Если капча/429/кнопка — решает внутри через `resolve_captcha_flow()`
3. Если критическая ошибка (403/407/not_detected) — возвращает соответствующий статус
4. Если каталог — парсит все карточки на странице
5. Определяет есть ли следующая страница и её URL

**Что возвращает (SinglePageResult):**
- status — статус выполнения (SUCCESS, CAPTCHA_FAILED, PROXY_BLOCKED, и т.д.)
- cards — список карточек с этой страницы (если успех)
- has_next — есть ли следующая страница
- next_url — URL следующей страницы (если есть)
- error_state — какой детектор сработал (при ошибке)
- error_url — URL страницы (при ошибке)

**Параметры:**
- page — Playwright Page (уже открыта на каталоге)
- fields — какие поля извлекать из карточек
- include_html — сохранять ли HTML карточек (по умолчанию False)
- max_captcha_attempts — лимит попыток капчи (по умолчанию 30)

**Не делает:**
- Не делает goto (страница уже открыта)
- Не накапливает данные между вызовами
- Не имеет continue_from

### parse_catalog — парсинг всех страниц

Парсит все страницы каталога с пагинацией. Внутри вызывает `parse_single_page` в цикле.

**Что делает:**
1. Вызывает `parse_single_page` для текущей страницы
2. Если успех — накапливает карточки, переходит на следующую страницу через `navigate_to_catalog`
3. При переходе делает retry (до 5 попыток) при таймауте
4. Повторяет пока не достигнут max_pages или не кончились страницы
5. При критической ошибке — возвращает результат с возможностью продолжения

**Что возвращает (CatalogParseResult):**
- status — статус выполнения
- listings — все собранные карточки (со всех страниц)
- meta — метаинформация (сколько страниц, сколько карточек)
- error_state, error_url — информация об ошибке
- resume_url, resume_page_number — информация для продолжения
- continue_from() — метод для продолжения после ошибки

**Параметры:**
- page — Playwright Page (уже открыта на каталоге)
- catalog_url — базовый URL каталога (для navigate_to_catalog)
- fields — какие поля извлекать из карточек
- max_pages — максимум страниц (None = без лимита)
- sort — сортировка (date, price_asc, price_desc, mileage_asc)
- start_page — с какой страницы начинать (для расчёта resume)
- include_html — сохранять ли HTML карточек (по умолчанию False)
- max_captcha_attempts — лимит попыток капчи (по умолчанию 30)
- load_timeout — таймаут загрузки страницы (по умолчанию 3 минуты)
- load_retries — количество retry при таймауте (по умолчанию 5)

---

## Классификация состояний страницы

### Состояния, при которых парсим (успех)

- **Каталог** (`catalog_page_detector`) — парсим карточки, переходим на следующую страницу.

### Состояния, которые решаем внутри

- **Капча Geetest** (`captcha_geetest_detector`) — вызываем решатель капчи, после успеха проверяем состояние заново.

- **Rate-limit 429** (`proxy_block_429_detector`) — это тоже капча, решаем так же.

- **Кнопка "Продолжить"** (`continue_button_detector`) — вызываем `resolve_captcha_flow()`, он сам обработает кнопку.

### Критические ошибки (возвращаем управление)

- **Блокировка IP 403** (`proxy_block_403_detector`) — прокси заблокирован, нужна новая страница с новым прокси.

- **Auth 407** (`proxy_auth_407_detector`) — проблема с авторизацией прокси, нужна новая страница.

- **Не распознано** (`not_detected`) — ни один детектор не сработал, что-то пошло не так, нужна новая страница.

- **Таймаут загрузки** — страница не загрузилась за отведённое время, нужна новая страница.

- **Капча не решена** — после N попыток капча всё ещё не решена, возвращаем управление (возможно нужен другой прокси).

### Ошибки (неправильная страница)

- **Карточка товара** (`card_found_detector`) — это не каталог, а отдельное объявление. Ошибка в логике вызывающего кода.

- **Профиль продавца** (`seller_profile_detector`) — это не каталог. Ошибка.

- **Удалённое объявление** (`removed_or_not_found_detector`) — это не каталог. Ошибка.

---

## Flow работы

### Flow типичного использования

```python
from avito_library import navigate_to_catalog, parse_catalog

# 1. Создаём страницу с прокси
page = await browser.new_page()

# 2. Переходим на каталог через navigate_to_catalog
response = await navigate_to_catalog(
    page,
    "https://avito.ru/moskva/telefony",
    sort="date",
    start_page=1,
)

# 3. Парсим каталог (страница уже открыта, парсер НЕ делает первый goto)
result = await parse_catalog(
    page,
    "https://avito.ru/moskva/telefony",
    fields=["title", "price", "location"],
    max_pages=10,
    sort="date",        # нужен для navigate_to_catalog при переходе на следующие страницы
    start_page=1,       # нужен для расчёта resume_page_number
)

# 4. Обработка результата
if result.status == CatalogParseStatus.SUCCESS:
    for listing in result.listings:
        print(listing.title, listing.price)

elif result.status == CatalogParseStatus.PROXY_BLOCKED:
    # Создаём новую страницу с новым прокси
    new_page = await create_page_with_new_proxy()
    # Продолжаем с того места
    result = await result.continue_from(new_page)
```

### Flow parse_single_page (одна страница)

1. Страница уже открыта (внешний код сделал `navigate_to_catalog`).

2. Вызываем `detect_page_state()`.

3. Обработка состояния:
   - Каталог → парсим карточки, ищем next_url, возвращаем SUCCESS
   - Капча/429/кнопка → вызываем `resolve_captcha_flow()`, после успеха снова detect
   - 403/407/not_detected → возвращаем соответствующий статус
   - Карточка/профиль/удалённое → возвращаем WRONG_PAGE
   - Капча не решена после всех попыток → возвращаем CAPTCHA_FAILED

4. Если каталог — парсим все карточки через `load_catalog_cards()` и `extract_listing()`.

5. Ищем ссылку на следующую страницу через `get_next_page_url()`.

6. Возвращаем SinglePageResult с карточками, has_next, next_url.

### Flow parse_catalog (все страницы)

1. Страница уже открыта на каталоге (внешний код сделал `navigate_to_catalog`).

2. Вызываем `parse_single_page()` для текущей страницы.

3. Если успех — накапливаем карточки, увеличиваем счётчик страниц.

4. Если ошибка — возвращаем CatalogParseResult с накопленными данными и информацией для продолжения.

5. Проверяем лимит max_pages — если достигнут, возвращаем SUCCESS.

6. Если есть следующая страница — вызываем `navigate_to_catalog` с retry (до 5 попыток при таймауте).

7. Если переход успешен — повторяем с шага 2.

8. Если все retry исчерпаны — возвращаем LOAD_TIMEOUT.

9. Если следующей страницы нет — возвращаем SUCCESS.

### Flow continue_from (продолжение после ошибки)

1. Внешний код получил результат с критической ошибкой.

2. Внешний код блокирует старый прокси (если 403/407), создаёт новую страницу с новым прокси.

3. Внешний код вызывает `result.continue_from(new_page)`.

4. Метод `continue_from` определяет, нужен ли goto:
   - Если `skip_navigation=True` — не делаем goto (внешний код уже перешёл)
   - Если `skip_navigation=False` — делаем `navigate_to_catalog` на resume_url
   - Если `skip_navigation=None` — detect, если каталог — не goto, иначе goto

5. Продолжаем парсинг с того места где остановились.

6. Все ранее собранные карточки сохраняются, новые добавляются к ним.

7. Счётчик страниц продолжается с предыдущего значения.

---

## Что возвращают функции

### navigate_to_catalog

- `Response` — объект ответа Playwright (то же, что `page.goto()`)

### SinglePageResult (от parse_single_page)

**При успехе:**
- status: SUCCESS
- cards: список карточек с этой страницы
- has_next: есть ли следующая страница
- next_url: URL следующей страницы

**При ошибке:**
- status: PROXY_BLOCKED / PROXY_AUTH_REQUIRED / PAGE_NOT_DETECTED / CAPTCHA_FAILED / WRONG_PAGE
- cards: пустой список
- error_state: какой детектор сработал
- error_url: URL страницы

### CatalogParseResult (от parse_catalog)

**При успехе:**
- status: SUCCESS или EMPTY
- listings: все собранные карточки (со всех страниц)
- meta: метаинформация (processed_pages, processed_cards)

**При критической ошибке:**
- status: PROXY_BLOCKED / PROXY_AUTH_REQUIRED / PAGE_NOT_DETECTED / LOAD_TIMEOUT / CAPTCHA_FAILED
- listings: карточки, которые успели собрать до ошибки
- error_state: какой детектор сработал
- error_url: URL страницы где произошла ошибка
- resume_url: URL для продолжения
- resume_page_number: номер страницы для продолжения
- continue_from(): метод для продолжения парсинга

**При ошибке неправильной страницы:**
- status: WRONG_PAGE
- error_state: какой детектор сработал (card_found / seller_profile / removed)
- error_url: URL страницы
- Продолжение невозможно — это ошибка в логике вызывающего кода

---

## Обработка капчи

Когда детектор возвращает состояние капчи (или 429, что тоже капча):

1. Вызываем `resolve_captcha_flow()` с одной попыткой.

2. После вызова состояние уже определено внутри решателя капчи.

3. Если состояние стало каталогом — продолжаем парсинг.

4. Если состояние осталось капчей — повторяем попытку.

5. Если достигнут лимит попыток (по умолчанию 30, можно передать другое значение) — возвращаем CAPTCHA_FAILED.

6. Если состояние стало критической ошибкой (403/407/not_detected) — возвращаем соответствующий статус.

---

## Обработка кнопки "Продолжить"

Когда детектор возвращает состояние кнопки "Продолжить" — вызываем `resolve_captcha_flow()`.

Решатель капчи сам обрабатывает кнопку внутри (вызывает `press_continue_and_detect()`).

---

## Навигация между страницами

Навигация происходит внутри `parse_catalog` (не в `parse_single_page`).

После успешного парсинга одной страницы каталога:

1. Ищем ссылку на следующую страницу через `get_next_page_url()`.

2. Если ссылка найдена и не достигнут лимит max_pages — вызываем `navigate_to_catalog` с URL следующей страницы.

3. Таймаут загрузки — 3 минуты (180 секунд).

4. При таймауте — retry до 5 попыток (параметр load_retries).

5. Если все retry исчерпаны — возвращаем LOAD_TIMEOUT с информацией для продолжения.

6. После успешного перехода — вызываем `parse_single_page` для новой страницы (там будет detect).

**Важно:** Для перехода на следующие страницы используется `navigate_to_catalog`, но параметры sort/start_page берутся из next_url (он уже содержит нужные query-параметры от кнопки пагинации).

---

## Лимит страниц (max_pages)

- Передаётся при первом вызове `parse_catalog`.

- Счётчик обработанных страниц сохраняется между вызовами `continue_from()`.

- Пример: max_pages=10, обработали 5 страниц, произошла ошибка, продолжили — осталось обработать ещё 5.

- Когда счётчик достигает лимита — возвращаем SUCCESS, даже если есть ещё страницы.

---

## Хранение состояния для continue_from

Результат `CatalogParseResult` хранит внутри всё необходимое для продолжения:

**Публичные поля:**
- status — статус выполнения
- listings — все собранные карточки
- meta — метаинформация
- error_state, error_url — информация об ошибке
- resume_url — URL для продолжения
- resume_page_number — номер страницы для продолжения

**Внутреннее состояние (для continue_from):**
- Параметры первого вызова: catalog_url, fields, max_pages, sort, start_page, и т.д.
- Счётчик обработанных страниц
- Накопленные карточки

**Метод continue_from(new_page, skip_navigation=None):**
- Принимает новую страницу Playwright
- Параметр skip_navigation:
  - True — не делать goto (внешний код уже перешёл на нужную страницу)
  - False — делать `navigate_to_catalog` на resume_url
  - None (по умолчанию) — автоопределение: если detect вернул каталог — не goto, иначе goto
- Возвращает новый CatalogParseResult с объединёнными данными

---

## Используемые компоненты библиотеки

### Детекторы (из `avito_library/detectors/`)

- `detect_page_state.py` — главный роутер, вызывает детекторы по приоритету
- `catalog_page_detector.py` — определяет страницу каталога
- `captcha_geetest_detector.py` — определяет капчу Geetest
- `proxy_block_403_detector.py` — определяет блокировку 403
- `proxy_block_429_detector.py` — определяет rate-limit 429
- `proxy_auth_407_detector.py` — определяет проблему с авторизацией прокси
- `continue_button_detector.py` — определяет кнопку "Продолжить"
- `card_found_detector.py` — определяет карточку товара
- `seller_profile_detector.py` — определяет профиль продавца
- `removed_or_not_found_detector.py` — определяет удалённое объявление

### Решатель капчи (из `avito_library/capcha/`)

- `resolver.py` — функция `resolve_captcha_flow()` для решения капчи

### Вспомогательные функции (из текущего `catalog_parser/`)

- `helpers.py` — `load_catalog_cards()`, `extract_listing()`, `get_next_page_url()`, `apply_sort()`, `apply_start_page()`
- `models.py` — `CatalogListing`, `CatalogParseMeta` (нужно расширить)

---

## Отличия от текущей реализации

### Убираем

- Двойной goto на первую страницу
- Глобальный `_EXCHANGE` синглтон в `steam.py`
- Функции `wait_for_page_request()` и `supply_page()`
- Вызов `press_continue_and_detect()` на каждой странице (используем только `detect_page_state()`)
- Мёртвый код с дублирующими проверками состояний
- Файл `steam.py` полностью (сделать бэкап перед удалением)

### Добавляем

- Функция `navigate_to_catalog` — обёртка над goto с параметрами каталога
- Три функции: `navigate_to_catalog`, `parse_single_page` и `parse_catalog`
- Два типа результатов: `SinglePageResult` и `CatalogParseResult`
- Объект результата с методом `continue_from()`
- Хранение состояния парсера внутри объекта результата
- Явные статусы для разных типов ошибок
- Параметр `skip_navigation` с автоопределением
- Параметр `max_captcha_attempts` для настройки лимита попыток капчи
- Параметр `load_timeout` (3 минуты по умолчанию)
- Параметр `load_retries` (5 попыток по умолчанию)

### Сохраняем

- Логику парсинга карточек (`load_catalog_cards`, `extract_listing`)
- Логику навигации (`get_next_page_url`)
- Модель данных `CatalogListing`
- Интеграцию с решателем капчи
- Хелперы `apply_sort`, `apply_start_page` (используются в `navigate_to_catalog`)

---

## Файловая структура

Создаём новый файл для реализации (старые файлы сохраняем как бэкап):

- `navigation.py` — новый файл с функцией `navigate_to_catalog`
- `catalog_parser_v2.py` — новая реализация с `parse_single_page` и `parse_catalog`
- `steam.py` → `steam.py.bak` — бэкап старого оркестратора (потом удалить)
- `models.py` — расширить новыми типами (`SinglePageResult`, обновить `CatalogParseResult`)

---

## Статусы (CatalogParseStatus)

Набор статусов для обеих функций:

**Успех:**
- SUCCESS — всё обработано (включая пустой каталог с 0 карточек)

**Критические ошибки (нужна новая страница):**
- PROXY_BLOCKED — HTTP 403, IP заблокирован
- PROXY_AUTH_REQUIRED — HTTP 407, проблема с авторизацией прокси
- PAGE_NOT_DETECTED — ни один детектор не сработал
- LOAD_TIMEOUT — таймаут загрузки (после всех retry)
- CAPTCHA_FAILED — капча не решена после всех попыток

**Ошибки (неправильная страница):**
- WRONG_PAGE — страница не является каталогом (карточка/профиль/удалённое)

---

## Технические решения

### 1. navigate_to_catalog при переходе на страницы 2, 3, 4...

При переходе на следующие страницы `next_url` уже содержит все query-параметры (получен из кнопки пагинации).

**Решение:** Передаём полный URL как есть, без дополнительных параметров:
```python
# next_url = "https://avito.ru/moskva/telefony?p=2&s=104"
await navigate_to_catalog(page, next_url)  # sort и start_page не передаём
```

Функция `navigate_to_catalog` должна понимать, что если URL уже содержит `?p=` и `?s=`, то не добавлять их повторно.

### 2. Обработка капчи (max_captcha_attempts)

Параметр `max_captcha_attempts` определяет сколько раз вызывать `resolve_captcha_flow()`.

**Решение:** Вызываем `resolve_captcha_flow()` в цикле с `max_attempts=1`:
```python
for attempt in range(max_captcha_attempts):
    _, solved = await resolve_captcha_flow(page, max_attempts=1)
    if solved:
        state = await detect_page_state(page)
        if state == CATALOG_DETECTOR_ID:
            break  # Успех — продолжаем парсинг
        if state in CRITICAL_ERRORS:
            return error_result  # Критическая ошибка
    # Не решена — следующая попытка
else:
    return CatalogParseStatus.CAPTCHA_FAILED
```

### 3. Retry при таймауте загрузки

При таймауте `page.goto()` делаем retry (по умолчанию 5 попыток) — сеть может временно лагать.

**Важно:** Retry имеет смысл только при таймауте. Если после загрузки страницы детектор вернул критическую ошибку (403/407/not_detected) — это явная ошибка, сразу возвращаем управление без retry.

```python
for retry in range(load_retries):
    try:
        await navigate_to_catalog(page, next_url, timeout=load_timeout)
        break  # Успешно загрузили
    except TimeoutError:
        if retry == load_retries - 1:
            return LOAD_TIMEOUT  # Все retry исчерпаны
        # Пробуем ещё раз

# После успешной загрузки — detect
state = await detect_page_state(page)
if state == PROXY_BLOCKED:
    return PROXY_BLOCKED  # Сразу возвращаем, без retry
```

### 4. Параметр include_html

Параметр `include_html` сохраняется из текущей реализации.

**Сигнатуры:**
```python
async def parse_single_page(..., include_html: bool = False) -> SinglePageResult
async def parse_catalog(..., include_html: bool = False) -> CatalogParseResult
```

### 5. Пустой каталог

Если на странице каталога 0 карточек — возвращаем `SUCCESS` с пустым списком, не `EMPTY`.

```python
SinglePageResult(status=SUCCESS, cards=[], has_next=False, next_url=None)
```

Статус `EMPTY` НЕ используется (можно удалить из enum).

### 6. Реализация CatalogParseResult

Реализуем как `@dataclass` с методом `continue_from()`:

```python
@dataclass
class CatalogParseResult:
    # Публичные поля
    status: CatalogParseStatus
    listings: list[CatalogListing]
    meta: CatalogParseMeta
    error_state: str | None = None
    error_url: str | None = None
    resume_url: str | None = None
    resume_page_number: int | None = None

    # Приватные поля (не показываются в repr)
    _catalog_url: str = field(default="", repr=False)
    _fields: set = field(default_factory=set, repr=False)
    _max_pages: int | None = field(default=None, repr=False)
    _sort: str | None = field(default=None, repr=False)
    _start_page: int = field(default=1, repr=False)
    _include_html: bool = field(default=False, repr=False)
    _max_captcha_attempts: int = field(default=30, repr=False)
    _load_timeout: int = field(default=180_000, repr=False)
    _load_retries: int = field(default=5, repr=False)
    _processed_pages: int = field(default=0, repr=False)

    async def continue_from(
        self,
        new_page: Page,
        skip_navigation: bool | None = None,
    ) -> 'CatalogParseResult':
        """Продолжает парсинг с новой страницей."""
        # Реализация внутри
        ...
```

### 7. Реализация SinglePageResult

Простой `@dataclass` без методов:

```python
@dataclass
class SinglePageResult:
    status: CatalogParseStatus
    cards: list[CatalogListing]
    has_next: bool
    next_url: str | None = None
    error_state: str | None = None
    error_url: str | None = None
```
