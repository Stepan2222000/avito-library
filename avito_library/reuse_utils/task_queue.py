"""Очередь задач для распределения работы между асинхронными воркерами.

Модуль повторяет боевую очередь из наших парсеров, но переписан так, чтобы
оставаться независимым от конкретного домена. Ключ задачи может описывать
что угодно: URL каталога, идентификатор продавца, параметры аналитики.

Основные принципы:
* **Уникальность** — один и тот же `task_key` никогда не выдаётся двум
  воркерам одновременно.
* **Учёт попыток** — при каждом возврате задачи счётчик попыток увеличивается;
  как только он превысит `max_attempts`, задача исключается из очереди.
* **Пауза** — если внешние ресурсы (прокси, страницы Playwright) временно
  недоступны, очередь можно остановить и возобновить позже.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Deque, Dict, Hashable, Iterable, Optional, Tuple


def log_event(event: str, **payload: object) -> None:
    """Простейший логгер на случай отсутствия интеграции.

    По умолчанию выводим сообщение в `stdout`, чтобы у потребителя библиотеки
    была хотя бы базовая видимость происходящего. При необходимости можно
    заменить функцию на собственную реализацию.
    """
    if payload:
        extras = " ".join(f"{key}={value!r}" for key, value in payload.items())
        print(f"event={event} {extras}")
    else:
        print(f"event={event}")


class TaskState(str, Enum):
    """Внутренние состояния задачи в очереди :class:`TaskQueue`."""

    PENDING = "pending"  # задача поставлена, но ещё не выдана
    IN_PROGRESS = "in_progress"  # задача выдана воркеру и обрабатывается
    RETURNED = "returned"  # задача возвращена после попытки и ждёт повторной выдачи


@dataclass(slots=True)
class ProcessingTask:
    """Описывает одну логическую задачу в очереди."""

    task_key: Hashable
    payload: Any
    attempt: int = 1
    state: TaskState = TaskState.PENDING
    last_proxy: Optional[str] = None
    enqueued_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_result: Optional[str] = None

    def bump_attempt(self) -> None:
        """Увеличить счётчик попыток и обновить отметку `updated_at`."""
        self.attempt += 1
        self.touch()

    def set_state(self, state: TaskState) -> None:
        """Задать новое состояние и обновить отметку времени."""
        self.state = state
        self.touch()

    def set_last_proxy(self, proxy: Optional[str]) -> None:
        """Запомнить прокси, использованный в предыдущей попытке."""
        self.last_proxy = proxy
        self.touch()

    def touch(self) -> None:
        """Проставить текущий момент времени (UTC) в `updated_at`."""
        self.updated_at = datetime.now(timezone.utc)


class TaskQueue:
    """Очередь FIFO, защищённая `asyncio.Lock`.

    Вся информация хранится в памяти. Если нужен персистентный бэкенд,
    его следует добавить во внешнем приложении. Благодаря локам несколько
    воркеров внутри одного события `asyncio` могут безопасно вызывать `get()` и
    `retry()`, не порождая гонок.
    """

    def __init__(self, *, max_attempts: int) -> None:
        if max_attempts < 1:
            raise ValueError("параметр max_attempts должен быть >= 1")
        self._max_attempts = max_attempts
        self._lock = asyncio.Lock()
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._paused = False
        self._pending_order: Deque[Hashable] = deque()
        self._tasks: Dict[Hashable, ProcessingTask] = {}

    async def put_many(self, items: Iterable[Tuple[Hashable, Any]]) -> int:
        """Добавить сразу несколько пар `(task_key, payload)`.

        Повторяющиеся ключи пропускаются. Возвращается количество реально
        вставленных задач.
        """
        inserted = 0
        async with self._lock:
            for task_key, payload in items:
                if task_key in self._tasks:
                    continue
                task = ProcessingTask(task_key=task_key, payload=payload)
                self._tasks[task_key] = task
                self._pending_order.append(task_key)
                inserted += 1
        return inserted

    async def get(self) -> Optional[ProcessingTask]:
        """Выдать следующую задачу или `None`, если очередь пуста."""
        while True:
            await self._pause_event.wait()
            async with self._lock:
                if self._paused:
                    continue
                while self._pending_order:
                    task_key = self._pending_order.popleft()
                    task = self._tasks.get(task_key)
                    if task is None:
                        continue
                    if task.state not in (TaskState.PENDING, TaskState.RETURNED):
                        continue
                    task.set_state(TaskState.IN_PROGRESS)
                    return task
                return None

    async def mark_done(self, task_key: Hashable) -> None:
        """Удалить задачу из очереди после успешной обработки."""
        async with self._lock:
            self._tasks.pop(task_key, None)

    async def retry(self, task_key: Hashable, *, last_proxy: Optional[str] = None) -> bool:
        """Вернуть задачу в очередь и увеличить счётчик попыток."""
        async with self._lock:
            task = self._tasks.get(task_key)
            if task is None:
                return False
            task.set_last_proxy(last_proxy)
            task.bump_attempt()
            if task.attempt > self._max_attempts:
                self._tasks.pop(task_key, None)
                return False
            task.set_state(TaskState.RETURNED)
            self._pending_order.append(task_key)
        return True

    async def abandon(self, task_key: Hashable) -> None:
        """Удалить задачу без повторной постановки (непоправимая ошибка)."""
        async with self._lock:
            self._tasks.pop(task_key, None)

    async def pending_count(self) -> int:
        """Вернуть количество задач, которые ещё ожидают выдачи."""
        async with self._lock:
            return sum(
                1
                for task in self._tasks.values()
                if task.state in (TaskState.PENDING, TaskState.RETURNED)
            )

    async def pause(self, *, reason: str) -> bool:
        """Приостановить выдачу задач до вызова :meth:`resume`."""
        async with self._lock:
            if self._paused:
                return False
            self._paused = True
            self._pause_event.clear()
        log_event("queue_paused", reason=reason)
        return True

    async def resume(self, *, reason: str) -> bool:
        """Возобновить выдачу задач, если очередь ранее была на паузе."""
        async with self._lock:
            if not self._paused:
                return False
            self._paused = False
            self._pause_event.set()
        log_event("queue_resumed", reason=reason)
        return True
