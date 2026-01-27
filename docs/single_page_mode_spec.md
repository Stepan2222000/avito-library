# Спецификация: Режим single_page для parse_catalog()

## Обзор

Добавление параметра `single_page=True` в функцию `parse_catalog()` для упрощённого парсинга одной страницы каталога без инфраструктуры продолжения (`continue_from()`).

---

## Проблема

Текущая архитектура `parse_catalog()` оптимизирована для парсинга множества страниц:
- Сохраняет состояние в приватных полях (`_catalog_url`, `_fields`, `_max_pages` и т.д.)
- Поддерживает метод `continue_from()` для продолжения после блокировки
- Возвращает "тяжёлый" `CatalogParseResult` с данными для восстановления

Когда пользователю нужна только одна страница, вся эта инфраструктура избыточна:
- Не нужен `continue_from()` — при ошибке пользователь сам решает, что делать
- Не нужны приватные поля для восстановления состояния
- Не нужна логика пагинации и "очереди страниц"

---

## Решение

Добавить параметр `single_page: bool = False` в функцию `parse_catalog()`. При `single_page=True`:
- Парсится ровно одна страница (первая)
- Не сохраняются данные для `continue_from()`
- Метод `continue_from()` выбрасывает ошибку
- Возвращается тот же тип `CatalogParseResult`, но с пустыми приватными полями

---

## Детальное описание поведения

### Параметр single_page

**Сигнатура:**
```python
async def parse_catalog(
    page: Page,
    url: str | None = None,
    *,
    # ... существующие параметры ...
    single_page: bool = False,  # НОВЫЙ ПАРАМЕТР
    # ... остальные параметры ...
) -> CatalogParseResult:
```

**Значение по умолчанию:** `False` — обычный режим с пагинацией и `continue_from()`.

**При `single_page=True`:**
- Парсится только первая страница каталога
- Внутренне устанавливается `max_pages=1`
- Приватные поля результата остаются пустыми
- Метод `continue_from()` недоступен

---

### Валидация параметров

При `single_page=True` запрещены следующие параметры:

**1. Параметр `max_pages`:**
- Если передано `single_page=True` и `max_pages is not None` — выбросить ошибку
- Сообщение: `"max_pages нельзя указывать при single_page=True"`

**2. Параметр `start_page > 1`:**
- Если передано `single_page=True` и `start_page > 1` — выбросить ошибку
- Сообщение: `"start_page нельзя указывать при single_page=True"`
- Примечание: `start_page=1` (значение по умолчанию) разрешён

**Валидация выполняется в самом начале функции, до навигации и любых сетевых запросов (fail-fast подход).**

---

### Поток выполнения при single_page=True

Поток выполнения остаётся тем же, что и в обычном режиме:

1. Валидация параметров (новый шаг — проверка совместимости с single_page)
2. Построение URL (build_catalog_url / merge_url_with_params)
3. Навигация на страницу каталога (navigate_to_catalog)
4. Решение капчи если обнаружена (resolve_captcha_flow)
5. Применение механических фильтров если заданы (apply_mechanical_filters)
6. Парсинг одной страницы (parse_single_page)
7. Возврат результата

**Отличие:** Внутренне устанавливается `max_pages=1`, поэтому цикл пагинации выполняется ровно один раз.

---

### Механические фильтры

Механические фильтры (год, пробег, объём двигателя, привод, мощность, турбина, тип продавца) **применяются как обычно** при `single_page=True`.

Фильтры — это часть подготовки страницы, они не зависят от количества страниц для парсинга.

---

### Возвращаемый результат

При `single_page=True` возвращается тот же тип `CatalogParseResult`, но с особенностями:

**Публичные поля (заполняются как обычно):**

| Поле | Описание | Значение при успехе | Значение при ошибке |
|------|----------|---------------------|---------------------|
| `status` | Статус парсинга | `CatalogParseStatus.SUCCESS` | Соответствующий статус ошибки |
| `listings` | Список карточек | Карточки с одной страницы | Пустой список `[]` |
| `meta` | Метаинформация | `processed_pages=1`, `processed_cards=N` | `processed_pages=0`, `processed_cards=0` |
| `error_state` | ID детектора при ошибке | `None` | ID детектора (например, `"proxy_block_403_detector"`) |
| `error_url` | URL где произошла ошибка | `None` | URL страницы |
| `resume_url` | URL для продолжения | `None` (всегда) | `None` (всегда) |
| `resume_page_number` | Номер страницы для продолжения | `None` (всегда) | `None` (всегда) |

**Приватные поля (пустые значения):**

| Поле | Значение |
|------|----------|
| `_single_page` | `True` (новое поле) |
| `_catalog_url` | `""` (пустая строка) |
| `_fields` | `set()` (пустой set) |
| `_max_pages` | `None` |
| `_sort` | `None` |
| `_start_page` | `1` |
| `_include_html` | `False` |
| `_max_captcha_attempts` | `30` |
| `_load_timeout` | `180000` |
| `_load_retries` | `5` |
| `_processed_pages` | `0` |

---

### Поведение continue_from()

При вызове `result.continue_from(new_page)` на результате, полученном с `single_page=True`:

**Действие:** Выбрасывается исключение `ValueError`

**Сообщение об ошибке:** `"Невозможно продолжить парсинг: результат получен в режиме single_page"`

**Логика проверки:**
```python
async def continue_from(self, new_page, skip_navigation=None):
    if self._single_page:
        raise ValueError("Невозможно продолжить парсинг: результат получен в режиме single_page")
    # ... остальная логика ...
```

---

### Логирование

При входе в режим `single_page=True` добавляется запись в лог:

```python
if single_page:
    logger.info("Режим single_page: парсинг одной страницы")
```

Уровень: `INFO`

---

## Примеры использования

### Базовый пример

```python
from avito_library import parse_catalog, CatalogParseStatus

result = await parse_catalog(
    page,
    url="https://www.avito.ru/moskva/telefony",
    fields=["item_id", "title", "price"],
    single_page=True,
)

if result.status == CatalogParseStatus.SUCCESS:
    print(f"Спарсено {len(result.listings)} карточек")
    for card in result.listings:
        print(f"  {card.item_id}: {card.title}")
else:
    print(f"Ошибка: {result.status}")
    print(f"Детектор: {result.error_state}")
    print(f"URL: {result.error_url}")
```

### С фильтрами

```python
result = await parse_catalog(
    page,
    city="moskva",
    category="avtomobili",
    brand="bmw",
    # URL-фильтры
    body_type="Седан",
    price_min=500000,
    price_max=2000000,
    # Механические фильтры (применяются через Playwright)
    year_from=2018,
    mileage_to=100000,
    drive=["Полный"],
    # Параметры парсинга
    fields=["item_id", "title", "price", "location"],
    single_page=True,
)
```

### Обработка ошибок

```python
result = await parse_catalog(
    page,
    url="https://www.avito.ru/moskva/telefony",
    fields=["item_id", "title", "price"],
    single_page=True,
)

if result.status == CatalogParseStatus.PROXY_BLOCKED:
    # При single_page=True пользователь сам решает, что делать
    print("Прокси заблокирован, меняем прокси и пробуем снова")
    # Создаём новую страницу с другим прокси
    new_page = await create_page_with_new_proxy()
    # Запускаем парсинг заново (не continue_from!)
    result = await parse_catalog(
        new_page,
        url="https://www.avito.ru/moskva/telefony",
        fields=["item_id", "title", "price"],
        single_page=True,
    )

elif result.status == CatalogParseStatus.CAPTCHA_FAILED:
    print("Капча не решена")
    # Можно попробовать с другим прокси или подождать
```

### Ошибка при неправильном использовании

```python
# ОШИБКА: нельзя указывать max_pages при single_page=True
result = await parse_catalog(
    page,
    url="...",
    fields=["item_id"],
    single_page=True,
    max_pages=5,  # ValueError!
)

# ОШИБКА: нельзя указывать start_page > 1 при single_page=True
result = await parse_catalog(
    page,
    url="...",
    fields=["item_id"],
    single_page=True,
    start_page=3,  # ValueError!
)

# ОШИБКА: continue_from() недоступен при single_page=True
result = await parse_catalog(page, ..., single_page=True)
result = await result.continue_from(new_page)  # ValueError!
```

---

## Сравнение: single_page=True vs max_pages=1

| Аспект | `single_page=True` | `max_pages=1` |
|--------|-------------------|---------------|
| Количество страниц | 1 | 1 |
| `continue_from()` | Недоступен (ValueError) | Доступен |
| Приватные поля | Пустые | Заполнены |
| `resume_url` | Всегда `None` | Заполнен при ошибке |
| Использование памяти | Меньше | Больше |
| Сценарий | "Быстро спарсить одну страницу" | "Парсить с возможностью продолжения" |

**Когда использовать `single_page=True`:**
- Нужна только одна страница
- Не нужна возможность автоматического продолжения при ошибках
- Хочется упрощённый API без лишних полей

**Когда использовать `max_pages=1`:**
- Нужна одна страница, но с возможностью продолжения при ошибке
- Парсинг в рамках более сложного пайплайна с retry-логикой

---

## Изменения в файлах

### 1. avito_library/parsers/catalog_parser/models.py

**Добавить приватное поле в CatalogParseResult:**

```python
@dataclass
class CatalogParseResult:
    # ... существующие поля ...

    # Новое приватное поле
    _single_page: bool = field(default=False, repr=False)
```

**Модифицировать метод continue_from():**

```python
async def continue_from(self, new_page, skip_navigation=None):
    # Новая проверка в начале метода
    if self._single_page:
        raise ValueError("Невозможно продолжить парсинг: результат получен в режиме single_page")

    # ... остальная логика без изменений ...
```

---

### 2. avito_library/parsers/catalog_parser/catalog_parser_v2.py

**Добавить параметр в сигнатуру parse_catalog():**

```python
async def parse_catalog(
    page: Page,
    url: str | None = None,
    *,
    # ... существующие параметры ...
    single_page: bool = False,  # НОВЫЙ ПАРАМЕТР
    # ... остальные параметры ...
) -> CatalogParseResult:
```

**Добавить валидацию в начало функции (после объявления переменных):**

```python
# Валидация single_page режима
if single_page:
    if max_pages is not None:
        raise ValueError("max_pages нельзя указывать при single_page=True")
    if start_page > 1:
        raise ValueError("start_page нельзя указывать при single_page=True")

    logger.info("Режим single_page: парсинг одной страницы")
    max_pages = 1  # Внутренне устанавливаем лимит
```

**Модифицировать вызовы _build_result():**

Передавать параметр `single_page` во все вызовы `_build_result()`.

**Модифицировать функцию _build_result():**

```python
def _build_result(
    *,
    # ... существующие параметры ...
    single_page: bool = False,  # НОВЫЙ ПАРАМЕТР
) -> CatalogParseResult:
    # ... существующая логика создания meta ...

    if single_page:
        # Упрощённый результат без данных для продолжения
        return CatalogParseResult(
            status=status,
            listings=listings,
            meta=meta,
            error_state=error_state,
            error_url=error_url,
            resume_url=None,
            resume_page_number=None,
            _single_page=True,
            # Остальные приватные поля — значения по умолчанию
        )
    else:
        # Текущая логика без изменений
        return CatalogParseResult(
            status=status,
            listings=listings,
            meta=meta,
            # ... все поля как сейчас ...
        )
```

---

### 3. avito_library/parsers/catalog_parser/__init__.py

**Изменения не требуются.** Экспорты остаются прежними, `single_page` — это параметр существующей функции.

---

### 4. CLAUDE.md

**Обновить секцию parse_catalog() в описании парсеров:**

Добавить описание параметра `single_page` в таблицу параметров и добавить пример использования.

---

## Обратная совместимость

Изменения полностью обратно совместимы:

1. Параметр `single_page=False` по умолчанию — существующий код работает без изменений
2. Тип возвращаемого значения не меняется — всегда `CatalogParseResult`
3. Новое приватное поле `_single_page` имеет значение по умолчанию `False`
4. Метод `continue_from()` работает как раньше при `_single_page=False`

---

## Тестирование

### Тест-кейсы для ручной проверки

1. **Успешный парсинг одной страницы:**
   - Вызвать `parse_catalog(..., single_page=True)`
   - Проверить: `status == SUCCESS`, `listings` не пустой, `resume_url is None`

2. **Ошибка при single_page + max_pages:**
   - Вызвать `parse_catalog(..., single_page=True, max_pages=5)`
   - Проверить: выбрасывается `ValueError` с правильным сообщением

3. **Ошибка при single_page + start_page > 1:**
   - Вызвать `parse_catalog(..., single_page=True, start_page=3)`
   - Проверить: выбрасывается `ValueError` с правильным сообщением

4. **Ошибка при continue_from() в single_page режиме:**
   - Получить результат с `single_page=True`
   - Вызвать `result.continue_from(new_page)`
   - Проверить: выбрасывается `ValueError` с правильным сообщением

5. **Механические фильтры работают:**
   - Вызвать `parse_catalog(..., year_from=2020, single_page=True)`
   - Проверить: фильтры применены, карточки соответствуют фильтрам

6. **Обработка блокировки прокси:**
   - Вызвать с заблокированным прокси
   - Проверить: `status == PROXY_BLOCKED`, `error_url` заполнен, `resume_url is None`

---

## Дата создания спецификации

2025-12-24

## Статус

Готово к реализации
