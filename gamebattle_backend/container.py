import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from typing import AsyncIterator, Generic, TypeVar

import aiodocker
import aiodocker.stream

docker = aiodocker.Docker()


@dataclass
class Output:
    """A message from a container."""

    stream: int  # 1 for stdout, 2 for stderr?
    content: bytes
    timestamp: float


T = TypeVar("T")


class ReplayableStream(Generic[T]):  # With append(), close() and __aiter__
    """A replayable stream of items."""

    def __init__(self) -> None:
        self._items: list[T] = []
        self._closed = False
        self._subscribers: set[asyncio.Queue[tuple[T] | None]] = set()

    async def append(self, item: T) -> None:
        """Append an item to the stream."""
        if self._closed:
            raise ValueError("Stream is closed")

        self._items.append(item)
        for sub in self._subscribers:
            await sub.put((item,))

    async def close(self):
        """Close the stream."""
        self._closed = True
        for sub in self._subscribers:
            await sub.put(None)

    async def __aiter__(self) -> AsyncIterator[T]:
        for item in self._items:
            yield item

        if self._closed:
            return

        queue: asyncio.Queue[tuple[T] | None] = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            while True:
                element = await queue.get()
                if element is None:
                    return
                yield element[0]
        finally:
            self._subscribers.remove(queue)

    @property
    def accumulated(self) -> list[T]:
        """Return the accumulated items."""
        return self._items


class Container:
    """A docker container."""

    def __init__(
        self,
        image_name: str,
        memory_limit: int | None = None,
        cpu_limit: float | None = None,
    ):
        """Create a new container.

        Args:
            image_name (str): The name of the image to use.
            memory_limit (int | None): Memory limit in bytes.
            cpu_limit (float | None): CPU limit as fraction of cores (e.g., 0.25 = 25%).
        """
        self._output: ReplayableStream[Output] = ReplayableStream()
        self._image_name = image_name
        self._memory_limit = memory_limit
        self._cpu_limit = cpu_limit

        self._container: aiodocker.docker.DockerContainer | None = None
        self._stream: aiodocker.stream.Stream | None = None

        self.running = True

    async def start(self):
        """Start the container."""
        host_config: dict = {}
        if self._memory_limit is not None:
            host_config["Memory"] = self._memory_limit
        if self._cpu_limit is not None:
            host_config["NanoCpus"] = int(self._cpu_limit * 1e9)

        self._container = await docker.containers.create(
            config={
                "Image": self._image_name,
                "AttachStdout": True,
                "AttachStderr": True,
                "AttachStdin": True,
                "OpenStdin": True,
                "Tty": True,
                **({"HostConfig": host_config} if host_config else {}),
            },
        )
        self._stream = self._container.attach(
            logs=True,
            stdin=True,
            stdout=True,
            stderr=True,
        )
        await self._container.start()
        asyncio.create_task(self._receive())

    async def stop(self):
        """Stop the container."""
        if self._container is None:
            return
        # Just force-kill the container
        with contextlib.suppress(aiodocker.exceptions.DockerError):
            await self._container.kill(signal="SIGKILL")
        with contextlib.suppress(aiodocker.exceptions.DockerError):
            await self._container.wait()
        with contextlib.suppress(aiodocker.exceptions.DockerError):
            await self._container.delete()
        self._container = None

    def __del__(self):
        """Cleanup the container."""
        if self._container is not None:
            logging.warning(
                "Container %s (image %s) was not stopped",
                self._container.id,
                self._image_name,
            )
            asyncio.run(self.stop())

    async def send(self, message: bytes) -> None:
        """Send a message to the container."""
        if self._container is None or self._stream is None:
            return
        await self._stream.write_in(message)

    async def _receive(self) -> None:
        """Receive messages from the container."""
        if self._container is None or self._stream is None:
            return

        while True:
            message = await self._stream.read_out()

            if message is None:
                await self._output.close()
                self.running = False
                break

            await self._output.append(
                Output(
                    stream=message.stream,
                    content=message.data,
                    timestamp=time.time(),
                )
            )

    def receive(self) -> ReplayableStream[Output]:
        """Receive messages from the container."""
        return self._output

    async def resize(self, width: int, height: int) -> None:
        if self._container is None:
            return
        await self._container.resize(w=width, h=height)
