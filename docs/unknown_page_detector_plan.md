# План реализации unknown_page_detector

## Суть задачи

Нужно создать новый детектор `unknown_page_detector`, который будет ловить известные edge cases — страницы Avito, которые не являются ни каталогом, ни карточкой объявления, ни другими уже обрабатываемыми типами страниц.

Первый случай для детекции — **"Журнал Авито Авто"** (редакционная/блоговая страница). В будущем детектор будет пополняться другими паттернами по мере их обнаружения.

При срабатывании детектора парсеры должны возвращать статус **WRONG_PAGE** — это означает "мы поняли что за страница, но она не подходит для парсинга".

---

## Разница между статусами

| Статус | Значение |
|--------|----------|
| `WRONG_PAGE` | Мы понимаем что это за страница, но она не та что нужна (известный edge case) |
| `PAGE_NOT_DETECTED` | Вообще непонятно что за страница, ни один детектор не сработал |

---

## Логика детекции "Журнала Авито"

Детектор использует комбинацию признаков (все должны выполняться):

1. **Отсутствует селектор каталога** — нет `div[data-marker="catalog-serp"]`
2. **Отсутствует селектор карточки** — нет `span[data-marker="item-view/item-id"]`
3. **Присутствует слово "журнал"** в HTML страницы

Такая комбинация надёжно отличает редакционные страницы от обычных объявлений, потому что слово "журнал" само по себе может встречаться в текстах объявлений, но там всегда будут маркеры каталога или карточки.

---

## Позиция в порядке приоритета

Новый детектор будет **последним** в списке (10-я позиция):

```
1.  proxy_block_403_detector
2.  proxy_block_429_detector
3.  proxy_auth_407_detector
4.  captcha_geetest_detector
5.  removed_or_not_found_detector
6.  seller_profile_detector
7.  catalog_page_detector
8.  card_found_detector
9.  continue_button_detector
10. unknown_page_detector  <-- НОВЫЙ
```

Это логично, потому что детектор ловит только то, что не поймали все остальные детекторы.

---

## Возвращаемое значение детектора

Детектор возвращает строку `"unknown_page_detector"` при срабатывании или `False` если не сработал.

В будущем можно будет возвращать подтипы (например `"unknown_page_detector:avito_magazine"`), но пока достаточно общего идентификатора.

---

## Результат возвращаемый парсерами

При срабатывании `unknown_page_detector` парсеры возвращают результат со статусом `WRONG_PAGE`.

### card_parser.py

Внешняя программа получит:

```python
CardParseResult(
    status=CardParseStatus.WRONG_PAGE,  # "wrong_page"
    data=None
)
```

### catalog_parser

Внешняя программа получит:

```python
SinglePageResult(
    status=CatalogParseStatus.WRONG_PAGE,  # "wrong_page"
    cards=[],
    has_next=False,
    error_state="unknown_page_detector",  # ID сработавшего детектора
    error_url="https://...",              # URL страницы где сработал
)
```

### Как внешний код обрабатывает результат

```python
result = await parse_card(page, response, fields=["title", "price"])

if result.status == CardParseStatus.WRONG_PAGE:
    # Страница не та — пропускаем этот URL
    # Прокси НЕ блокируем (это не проблема прокси)
    logger.warning(f"Неправильная страница: {url}")
    continue

if result.status == CardParseStatus.PROXY_BLOCKED:
    # Прокси заблокирован — блокируем его
    proxy_pool.block(current_proxy)
```

**Важно:** `WRONG_PAGE` означает что страница не подходит для парсинга, но прокси работает нормально. Блокировать прокси не нужно.

---

## План реализации

### Шаг 1: Создать файл детектора

**Файл:** `avito_library/detectors/unknown_page_detector.py`

Что делает:
- Определяет константу `DETECTOR_ID = "unknown_page_detector"`
- Реализует асинхронную функцию `unknown_page_detector(page)` которая:
  - Проверяет отсутствие селектора каталога
  - Проверяет отсутствие селектора карточки
  - Проверяет наличие слова "журнал" в HTML
  - При совпадении всех условий делает скриншот и возвращает `DETECTOR_ID`
  - Иначе возвращает `False`

### Шаг 2: Зарегистрировать детектор в реестре

**Файл:** `avito_library/detectors/__init__.py`

Изменения:
- Импортировать `DETECTOR_ID` как `UNKNOWN_PAGE_DETECTOR_ID` и функцию `unknown_page_detector`
- Добавить в словарь `DETECTOR_FUNCTIONS` (в конец)
- Добавить в кортеж `DETECTOR_DEFAULT_ORDER` (последним элементом)
- Добавить `UNKNOWN_PAGE_DETECTOR_ID` в список `__all__`

### Шаг 3: Обновить card_parser.py

**Файл:** `avito_library/parsers/card_parser.py`

Изменения:
- Добавить `WRONG_PAGE = "wrong_page"` в enum `CardParseStatus` (сейчас его там нет)
- Добавить импорт `UNKNOWN_PAGE_DETECTOR_ID` из detectors
- В функции `parse_card()` добавить обработку:
  - Если состояние начинается с `"unknown_page_detector"` — вернуть `CardParseResult` со статусом `WRONG_PAGE`

### Шаг 4: Обновить catalog_parser_v2.py

**Файл:** `avito_library/parsers/catalog_parser/catalog_parser_v2.py`

Изменения:
- Добавить импорт `UNKNOWN_PAGE_DETECTOR_ID` из detectors
- Изменить логику проверки `_WRONG_PAGE_STATES`:
  - Сейчас: `if state in _WRONG_PAGE_STATES`
  - Станет: `if state in _WRONG_PAGE_STATES or state.startswith("unknown_page_detector")`

Это нужно потому что в будущем детектор может возвращать подтипы.

### Шаг 5: Обновить главный __init__.py

**Файл:** `avito_library/__init__.py`

Изменения:
- Добавить `UNKNOWN_PAGE_DETECTOR_ID` в импорт из `detectors`
- Добавить `UNKNOWN_PAGE_DETECTOR_ID` в список `__all__`

---

## Структура нового файла детектора

```
unknown_page_detector.py
├── Импорты (typing, playwright, debug)
├── Константы
│   ├── DETECTOR_ID = "unknown_page_detector"
│   ├── CATALOG_SELECTOR (для проверки отсутствия)
│   ├── CARD_SELECTOR (для проверки отсутствия)
│   └── EDGE_CASE_PHRASES (фразы для детекции)
├── Главная функция unknown_page_detector(page)
│   ├── Проверка отсутствия каталога
│   ├── Проверка отсутствия карточки
│   ├── Проверка наличия фраз
│   ├── Скриншот при срабатывании
│   └── Возврат DETECTOR_ID или False
└── Вспомогательные функции
    ├── _has_selector(page, selector)
    └── _safe_page_content(page)
```

---

## Расширяемость

В будущем для добавления новых edge cases нужно будет только:

1. Добавить новый паттерн в `EDGE_CASE_PHRASES` или создать отдельный набор условий
2. Опционально — возвращать подтип вида `"unknown_page_detector:new_case"`

Парсеры уже будут готовы к обработке благодаря проверке через `startswith()`.

---

## Файлы для изменения

| Файл | Действие |
|------|----------|
| `avito_library/detectors/unknown_page_detector.py` | Создать |
| `avito_library/detectors/__init__.py` | Изменить |
| `avito_library/parsers/card_parser.py` | Изменить |
| `avito_library/parsers/catalog_parser/catalog_parser_v2.py` | Изменить |
| `avito_library/__init__.py` | Изменить |
