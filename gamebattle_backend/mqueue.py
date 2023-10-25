"""A multiple-consumer resettable queue primitive."""
from __future__ import annotations
from contextlib import contextmanager
import threading
from typing import Iterator


class MQueue:
    """A multiple-consumer resettable queue primitive."""

    def __init__(self):
        self._condition = threading.Condition()
        self._data: bytes = b""
        self._closed = False

    def push(self, data: bytes) -> None:
        """Push data to the queue."""
        with self._condition:
            if self._closed:
                raise RuntimeError("Cannot push to a closed queue.")
            self._data += data
            self._condition.notify_all()

    def close(self) -> None:
        """Close the queue."""
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def __iter__(self) -> Iterator[str | None]:
        """Create an iterator."""
        cursor: int = 0
        while True:
            with self._condition:
                if self._closed:
                    yield None
                    return
                new_cursor = len(self._data)
                while new_cursor > cursor:
                    try:
                        break
                    except UnicodeDecodeError:
                        new_cursor -= 1
                if new_cursor > cursor:
                    self._condition.release()
                    yield self._data[cursor:new_cursor].decode("utf-8")
                    self._condition.acquire()
                    cursor = new_cursor
                else:
                    self._condition.wait(5)


class ClearableMQueue:
    """A multiple-consumer resettable queue primitive."""

    def __init__(self):
        self.mqueue = MQueue()
        self.swap_lock = threading.Lock()

    def get_current(self) -> MQueue:
        """Get the current mqueue for pushing."""
        with self.swap_lock:
            if self.mqueue._closed:
                self.mqueue = MQueue()
            return self.mqueue

    def clear(self) -> None:
        """Clear the current mqueue and create a new one."""
        self.mqueue.close()

    def __iter__(self) -> Iterator[str | None]:
        """Create an iterator."""
        with self.swap_lock:
            if self.mqueue._closed:
                self.mqueue = MQueue()
            return iter(self.mqueue)

    @contextmanager
    def __call__(self) -> Iterator[MQueue]:
        """Enter a context."""
        with self.swap_lock:
            if self.mqueue._closed:
                self.mqueue = MQueue()
            yield self.mqueue
