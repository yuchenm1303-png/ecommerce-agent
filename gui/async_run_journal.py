from __future__ import annotations

from queue import Empty, Queue
from threading import Thread
from pathlib import Path


class AsyncRunJournal:
    """Write runtime telemetry off the Qt GUI thread.

    QProcess stdout is delivered on the GUI event loop.  Runtime logging must not
    turn every output line into a synchronous open/write/close syscall on that
    same loop, because browser execution can emit large bursts.  This journal
    keeps one file handle on a tiny daemon writer and drains queued lines in
    batches.  The GUI thread only performs an in-memory Queue.put().
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

    def append(self, line: str) -> None:
        if self._closed or self._error is not None:
            return
        self._queue.put(str(line))

    def close(self, *, timeout: float = 1.5) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.put(self._STOP)
        if self._thread.is_alive():
            self._thread.join(max(0.0, float(timeout)))

    def _run(self) -> None:
        pending: list[str] = []
        pending_bytes = 0

        def flush(handle) -> None:  # noqa: ANN001
            nonlocal pending_bytes
            if not pending:
                return
            handle.write("\n".join(pending) + "\n")
            handle.flush()
            pending.clear()
            pending_bytes = 0

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
                        flush(handle)
                        return

                    text = str(item)
                    pending.append(text)
                    pending_bytes += len(text.encode("utf-8", errors="replace")) + 1
                    if len(pending) >= 96 or pending_bytes >= 64 * 1024:
                        flush(handle)
        except BaseException as exc:  # logging must never take the GUI down
            self._error = exc


__all__ = ["AsyncRunJournal"]
