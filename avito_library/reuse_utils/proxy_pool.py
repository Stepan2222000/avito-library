"""Кольцевой пул прокси с учётом блокировок и сохранением состояния.

Здесь собрана типовая логика работы с прокси в наших проектах. Каждый воркер
запрашивает прокси у пула, возвращает его по завершении и помечает адрес
заблокированным при критических ошибках (403/407). Пул отлично сочетается с
очередью задач из `reuse_utils.task_queue`.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence


def log_event(event: str, **payload: object) -> None:
    """Простейший логгер (аналогичный `task_queue.log_event`)."""
    if payload:
        extras = " ".join(f"{key}={value!r}" for key, value in payload.items())
        print(f"event={event} {extras}")
    else:
        print(f"event={event}")


@dataclass(slots=True)
class ProxyEndpoint:
    """Описание одного прокси-эндоинта."""

    address: str  # адрес в формате host:port
    auth: Optional[str] = None  # логин:пароль или токен
    is_blocked: bool = False
    last_used_at: Optional[datetime] = None
    failures: int = 0

    def as_playwright_arguments(self) -> dict[str, str]:
        """Преобразовать данные в аргументы для `chromium.launch`."""
        params: dict[str, str] = {"server": self.address}
        if self.auth:
            if ":" in self.auth:
                username, password = self.auth.split(":", 1)
            else:
                username, password = self.auth, ""
            params["username"] = username
            params["password"] = password
        return params


class ProxyPool:
    """Потокобезопасный пул прокси с файловым blacklist-ом."""

    def __init__(self, *, proxies_file: Path, blocked_file: Path) -> None:
        self._proxies_file = proxies_file
        self._blocked_file = blocked_file

        self._lock = asyncio.Lock()
        self._availability_event = asyncio.Event()
        self._proxies: List[ProxyEndpoint] = []
        self._proxy_map: Dict[str, ProxyEndpoint] = {}
        self._blocked: set[str] = set()
        self._in_use: set[str] = set()
        self._cursor = 0
        self._last_address: Optional[str] = None

    @classmethod
    async def create(cls, proxies_file: Path, blocked_file: Path) -> "ProxyPool":
        """Создать пул и загрузить состояние из файлов."""
        pool = cls(proxies_file=proxies_file, blocked_file=blocked_file)
        await pool.reload()
        return pool

    async def reload(self) -> int:
        """Перечитать список прокси и файл блокировок."""
        proxies = await self._read_proxies()
        blocked = await self._read_blocked()

        async with self._lock:
            previous_last = self._last_address
            self._proxies = proxies
            self._proxy_map = {proxy.address: proxy for proxy in proxies}
            self._blocked = blocked
            for proxy in proxies:
                proxy.is_blocked = proxy.address in blocked
            self._in_use = {addr for addr in self._in_use if addr in self._proxy_map}
            total = len(proxies)
            if total and previous_last:
                try:
                    idx = next(i for i, proxy in enumerate(proxies) if proxy.address == previous_last)
                except StopIteration:
                    self._cursor = 0
                else:
                    self._cursor = (idx + 1) % total
            else:
                self._cursor = 0
            has_available = self._has_unblocked_locked()
        self._set_availability_event(has_available)
        return len(proxies)

    async def acquire(self) -> Optional[ProxyEndpoint]:
        """Получить следующий доступный прокси или `None`, если их нет."""
        async with self._lock:
            total = len(self._proxies)
            if total == 0:
                self._set_availability_event(False)
                return None

            for _ in range(total):
                proxy = self._proxies[self._cursor]
                self._cursor = (self._cursor + 1) % total

                if proxy.address in self._blocked:
                    continue
                if proxy.address in self._in_use:
                    continue

                self._in_use.add(proxy.address)
                proxy.last_used_at = datetime.now(timezone.utc)
                self._last_address = proxy.address
                self._set_availability_event(True)
                return proxy

            has_available = self._has_unblocked_locked()
        self._set_availability_event(has_available)
        return None

    async def release(self, address: str) -> None:
        """Вернуть прокси в свободный пул после успешной работы."""
        async with self._lock:
            self._in_use.discard(address)
            has_available = self._has_unblocked_locked()
        self._set_availability_event(has_available)

    async def mark_blocked(self, address: str, *, reason: str) -> None:
        """Заблокировать прокси и записать событие в файл `blocked_file`."""
        timestamp = datetime.now(timezone.utc).isoformat()
        should_log = False

        async with self._lock:
            proxy = self._proxy_map.get(address)
            if proxy:
                proxy.is_blocked = True
                proxy.failures += 1
            if address not in self._blocked:
                self._blocked.add(address)
                should_log = True
            self._in_use.discard(address)
            has_available = self._has_unblocked_locked()

        if should_log:
            record = f"{timestamp}\t{address}\t{reason}\n"
            await asyncio.to_thread(self._append_blocked_record, record)
            log_event("proxy_blocked", proxy=address, reason=reason)
        self._set_availability_event(has_available)

    async def all_proxies(self) -> Sequence[ProxyEndpoint]:
        """Вернуть копию списка всех настроенных прокси."""
        async with self._lock:
            return list(self._proxies)

    async def all_blocked(self) -> bool:
        """Вернуть `True`, если свободных прокси не осталось."""
        async with self._lock:
            if not self._proxies:
                return True
            return not any(
                proxy.address not in self._blocked and proxy.address not in self._in_use
                for proxy in self._proxies
            )

    async def wait_for_unblocked(self) -> None:
        """Подождать появления хотя бы одного незаблокированного прокси."""
        await self._availability_event.wait()

    # ------------------------------------------------------------------ #
    # Вспомогательные методы                                             #
    # ------------------------------------------------------------------ #

    async def _read_proxies(self) -> List[ProxyEndpoint]:
        proxies: Dict[str, ProxyEndpoint] = {}
        lines = await asyncio.to_thread(self._read_lines, self._proxies_file)
        if not lines:
            return []
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            auth, address = self._split_auth(line)
            if address not in proxies:
                proxies[address] = ProxyEndpoint(address=address, auth=auth)
        return list(proxies.values())

    async def _read_blocked(self) -> set[str]:
        blocked: set[str] = set()
        lines = await asyncio.to_thread(self._read_lines, self._blocked_file)
        if not lines:
            return blocked
        for raw_line in lines:
            if not raw_line.strip():
                continue
            parts = raw_line.split("\t")
            if len(parts) >= 2:
                blocked.add(parts[1].strip())
        return blocked

    @staticmethod
    def _split_auth(entry: str) -> tuple[Optional[str], str]:
        if "@" in entry:
            auth, server = entry.split("@", 1)
            return (auth or None), server

        parts = entry.split(":")
        if len(parts) >= 3:
            host, port, *credentials = parts
            username = credentials[0] if credentials else ""
            password = ":".join(credentials[1:]) if len(credentials) > 1 else ""
            auth = None
            if username:
                auth = f"{username}:{password}" if password else username
            server = f"{host}:{port}"
            return auth, server

        return None, entry

    @staticmethod
    def _read_lines(path: Path) -> List[str]:
        try:
            return path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []

    def _append_blocked_record(self, record: str) -> None:
        self._blocked_file.parent.mkdir(parents=True, exist_ok=True)
        with self._blocked_file.open("a", encoding="utf-8") as fh:
            fh.write(record)

    def _has_unblocked_locked(self) -> bool:
        if not self._proxies:
            return False
        return any(
            proxy.address not in self._blocked and proxy.address not in self._in_use
            for proxy in self._proxies
        )

    def _set_availability_event(self, available: bool) -> None:
        if available:
            self._availability_event.set()
        else:
            self._availability_event.clear()
