"""A class representing a single object attached to a container's output."""
from __future__ import annotations
import asyncio
import contextlib
import os
import select
import threading
import time
from typing import Generic, Iterable, Iterator, TypeVar

import docker

T = TypeVar("T")


class AsyncIteratorWrapper(Generic[T]):
    def __init__(self, iterator: Iterable[T]) -> None:
        self.iterator = iterator
        self.queue: asyncio.Queue[tuple[T] | None] = asyncio.Queue()
        self.start_thread()

    async def __aiter__(self) -> AsyncIteratorWrapper[T]:
        return self

    async def __anext__(self) -> T:
        data = await self.queue.get()
        if data is None:
            raise StopAsyncIteration
        return data[0]

    def start_thread(self):
        threading.Thread(target=self.read_loop).start()

    def read_loop(self):
        for data in self.iterator:
            asyncio.run(self.queue.put((data,)))
        asyncio.run(self.queue.put(None))


class AttachedInstance:
    """A class representing a single object attached to a container's output."""

    def __init__(
        self,
        container: docker.models.containers.Container,
        stdin_fd: int,
        stdout_fd: int,
    ) -> None:
        self.container = container
        self.closed = False
        self.stdin: int = stdin_fd
        self.stdout: int = stdout_fd
        self.wait_for_exit_thread: threading.Thread | None = None
        self.stdin_thread: threading.Thread | None = None
        self.data = b""
        self.new_data = threading.Condition()

    def create_stdin(self) -> None:
        # self.stdin = self.container.attach_socket(
        #    params={
        #        "stdin": 1,
        #        "stdout": 1,
        #        "stderr": 1,
        #        "stream": 1,
        #    },
        # )
        # self.stdin._sock.setblocking(False)
        self.container.start()
        while not self.container.status == "running":
            time.sleep(0.1)
            self.container.reload()

    def read_stdin_loop(self) -> None:
        while not self.closed:
            try:
                ready, _, _ = select.select([self.stdout], [], [], 5)
                if not ready:
                    continue
                with self.new_data:
                    self.data += os.read(self.stdout, 1024)
                    self.new_data.notify_all()
            except OSError:
                with self.new_data:
                    self.closed = True
                    self.new_data.notify_all()

    def wait_for_exit(self) -> None:
        self.container.wait()
        self.closed = True
        with contextlib.suppress(OSError):
            os.close(self.stdout)
            os.close(self.stdin)
        with self.new_data:
            self.new_data.notify_all()

    def close(self) -> None:
        with self.new_data:
            self.closed = True
            with contextlib.suppress(OSError):
                os.close(self.stdout)
                os.close(self.stdin)
            self.new_data.notify_all()
        self.container.wait()

    def start_stdin_thread(self) -> None:
        with self.new_data:
            if self.stdin_thread is not None:
                return
            self.create_stdin()
            self.stdin_thread = threading.Thread(target=self.read_stdin_loop)
            self.stdin_thread.start()

    def start_wait_for_exit_thread(self) -> None:
        with self.new_data:
            if self.wait_for_exit_thread is not None:
                return
            self.wait_for_exit_thread = threading.Thread(target=self.wait_for_exit)
            self.wait_for_exit_thread.start()

    def start(self) -> None:
        self.start_stdin_thread()
        self.start_wait_for_exit_thread()
        self.container.start()

    def send(self, data: str) -> None:
        if self.stdin is None:
            return
        with self.new_data:
            os.write(self.stdin, data.encode("utf-8"))
            if self.stdin != self.stdout:
                # We assume properly set up pipes
                self.data += data.encode("utf-8")
            self.new_data.notify_all()

    def decode_data(self, data: bytes, start_pointer: int) -> tuple[str, int]:
        """Decode the data from the start pointer."""
        new_pointer: int = len(data)
        while new_pointer > start_pointer:
            try:
                return data[start_pointer:new_pointer].decode("utf-8"), new_pointer
            except UnicodeDecodeError:
                new_pointer -= 1
                continue
        return "", new_pointer

    def __iter__(self) -> Iterator[str]:
        pointer = 0
        while not self.closed:
            with self.new_data:
                decoded_data, pointer = self.decode_data(self.data, pointer)
                if not decoded_data:
                    self.new_data.wait()
            if decoded_data:
                yield decoded_data

    def __aiter__(self) -> AsyncIteratorWrapper[str]:
        return AsyncIteratorWrapper(self)
