from __future__ import annotations

import os
from pathlib import Path
from queue import Empty, Queue
from threading import Thread


class AsyncRunJournal:
    """FIFO runtime journal with an explicit durability/error contract.

    Every string accepted by ``append`` is queued in order. ``close`` drains the
    queue, flushes and fsyncs the file, then returns the writer exception (if any).
    Callers can therefore distinguish a complete durable log from a logging-I/O
    failure instead of silently assuming that a daemon writer succeeded.
    """

    _STOP = object()

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._queue: Queue[str | object] = Queue()
        self._closed = False
        self._error: BaseException | None = None
        self._thread = Thread(
            target=self._run,
            name=f"run-journal:{self.path.name}",
            daemon=True,
        )
        self._thread.start()

    @property
    def error(self) -> BaseException | None:
        return self._error

    @property
    def error_text(self) -> str:
        error = self._error
        return f"{type(error).__name__}: {error}" if error is not None else ""

    def append(self, line: str) -> bool:
        if self._closed or self._error is not None:
            return False
        self._queue.put(str(line))
        return True

    def close(self) -> BaseException | None:
        if not self._closed:
            self._closed = True
            self._queue.put(self._STOP)
            self._thread.join()
        return self._error

    def _run(self) -> None:
        pending: list[str] = []
        pending_bytes = 0

        def flush(handle, *, durable: bool = False) -> None:  # noqa: ANN001
            nonlocal pending_bytes
            if pending:
                handle.write("\n".join(pending) + "\n")
                pending.clear()
                pending_bytes = 0
            handle.flush()
            if durable:
                os.fsync(handle.fileno())

        try:
            with self.path.open("a", encoding="utf-8", buffering=64 * 1024) as handle:
                while True:
                    try:
                        item = self._queue.get(timeout=0.15)
                    except Empty:
                        flush(handle)
                        continue

                    if item is self._STOP:
                        while True:
                            try:
                                tail = self._queue.get_nowait()
                            except Empty:
                                break
                            if tail is self._STOP:
                                continue
                            text = str(tail)
                            pending.append(text)
                            pending_bytes += len(text.encode("utf-8", errors="replace")) + 1
                        flush(handle, durable=True)
                        return

                    text = str(item)
                    pending.append(text)
                    pending_bytes += len(text.encode("utf-8", errors="replace")) + 1
                    if len(pending) >= 96 or pending_bytes >= 64 * 1024:
                        flush(handle)
        except BaseException as exc:  # logging failure is surfaced through close/error
            self._error = exc


__all__ = ["AsyncRunJournal"]
