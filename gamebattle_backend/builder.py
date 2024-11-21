import glob
import os
import tarfile
from io import BytesIO

import aiodocker
import yaml

from .common import GameMeta


async def build_app(app_path: str, app_file: str, tag: str):
    """Build a Docker image from an app directory using a specific context structure.

    Args:
        app_path: Path to the app directory
        app_file: Name of the Python file to run (e.g., "app.py")
        tag: Tag for the built image
    """
    docker = aiodocker.Docker()

    # Dockerfile content with parameterized app file
    dockerfile = f"""FROM python:3.12-alpine
WORKDIR /usr/src/app
COPY project/ .
CMD ["python", "{app_file}"]"""

    tar_buffer = BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w:gz") as tar:
        # Create the project directory in the tar
        for root, _, files in os.walk(app_path):
            for file in files:
                file_path = os.path.join(root, file)
                # Skip any Dockerfile in the app directory
                if file == "Dockerfile":
                    continue
                # Add files under the 'project' directory
                arcname = os.path.join("project", os.path.relpath(file_path, app_path))
                tar.add(file_path, arcname=arcname)

        # Add the Dockerfile at the root level
        dockerfile_info = tarfile.TarInfo(name="Dockerfile")
        dockerfile_content = dockerfile.encode("utf-8")
        dockerfile_info.size = len(dockerfile_content)
        tar.addfile(dockerfile_info, BytesIO(dockerfile_content))

    tar_buffer.seek(0)

    try:
        await docker.images.build(
            fileobj=tar_buffer, encoding="gzip", tag=tag, path_dockerfile="Dockerfile"
        )
    finally:
        await docker.close()
        tar_buffer.close()


class GameBuilder:
    def __init__(self, games_path: str) -> None:
        self._games_path = games_path

    async def build(self, metadata: GameMeta) -> None:
        """Build a game."""
        app_path = os.path.join(self._games_path, metadata.id)
        await build_app(app_path, metadata.file, metadata.image_name)

    async def scan(self) -> list[GameMeta]:
        """Scan the games folder for games."""
        indexes = glob.glob(os.path.join(self._games_path, "*.yaml"))
        games: list[GameMeta] = []
        for i, index in enumerate(indexes):
            with open(index, "r", encoding="utf-8") as file:
                data = yaml.safe_load(file)
                game = GameMeta(**data)
                games.append(game)

                print(
                    f"[{i + 1}/{len(indexes)}] Building {game.name} by {game.email}",
                    flush=True,
                )
                await self.build(game)
        print("Finished building games", flush=True)
        return games
