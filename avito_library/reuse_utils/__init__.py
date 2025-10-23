"""Инфраструктурные блоки, упрощающие работу с `avito-library`.

В пакете собраны проверенные временем паттерны из рабочих парсеров:

* :mod:`task_queue` — очередь задач с учётом повторных попыток;
* :mod:`proxy_pool` — кольцевая раздача прокси с файловым blacklist-ом.

Пример использования::

    from avito_library.reuse_utils.task_queue import TaskQueue
"""

from .task_queue import ProcessingTask, TaskQueue, TaskState
from .proxy_pool import ProxyEndpoint, ProxyPool

__all__ = [
    "ProcessingTask",
    "ProxyEndpoint",
    "ProxyPool",
    "TaskQueue",
    "TaskState",
]
