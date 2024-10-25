"""A class representing a single object attached to a container's output."""

from __future__ import annotations

import asyncio
import socket
from typing import AsyncIterator

import docker


class AttachedInstance:
    """A class representing a single object attached to a container's output."""

    def __init__(
        self,
        container: docker.models.containers.Container,
    ) -> None:
        self.container = container
        self.wait_for_exit_task: asyncio.Task | None = None
        self.stdin_task: asyncio.Task | None = None
        self.data = b""
        self.new_data = asyncio.Condition()

        self.closed = False

        self.container_socket = container.attach_socket(
            params={"stdin": 1, "stdout": 1, "stderr": 1, "stream": 1, "logs": 1}
        )
        self.container_socket._sock.setblocking(False)  # Non-blocking socket

    async def register_data(self, data: bytes):
        async with self.new_data:
            self.data += data
            # 16 MB limit - kill if exceeded
            if len(self.data) > 1024 * 1024 * 16:
                self.container.kill(signal=9)
                self.data += b"\n\nKilled due to exceeding 16 MB output limit."
            self.new_data.notify_all()

    def on_container_output(self):
        try:
            output = self.container_socket._sock.recv(1024)
            if output:
                asyncio.create_task(self.register_data(output))
        except socket.error as e:
            print(f"Socket error: {e}", flush=True)

    async def wait_for_exit(self) -> None:
        await asyncio.get_event_loop().run_in_executor(None, self.container.wait)
        self.closed = True
        async with self.new_data:
            self.new_data.notify_all()
        asyncio.get_event_loop().remove_reader(self.container_socket._sock)

    async def close(self) -> None:
        async with self.new_data:
            self.closed = True
            self.new_data.notify_all()
        await asyncio.get_event_loop().run_in_executor(None, self.container.wait)

    async def start_stdin_task(self) -> None:
        async with self.new_data:
            if self.stdin_task is not None:
                return
            asyncio.get_event_loop().add_reader(
                self.container_socket._sock, self.on_container_output
            )

    async def start_wait_for_exit_task(self) -> None:
        async with self.new_data:
            if self.wait_for_exit_task is not None:
                return
            self.wait_for_exit_task = asyncio.create_task(self.wait_for_exit())

    async def start(self) -> None:
        await self.start_stdin_task()
        await self.start_wait_for_exit_task()
        self.container.start()

    async def send(self, data: str) -> None:
        self.container_socket._sock.send(data.encode("utf-8"))

    async def resize(self, width: int, height: int) -> None:
        self.container.resize(width=width, height=height)

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

    async def __aiter__(self) -> AsyncIterator[str]:
        pointer = 0
        while not self.closed:
            async with self.new_data:
                decoded_data, pointer = self.decode_data(self.data, pointer)
                if not decoded_data:
                    await self.new_data.wait()
            if decoded_data:
                yield decoded_data
