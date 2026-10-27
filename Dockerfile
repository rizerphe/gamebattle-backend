FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY . .
RUN uv sync --locked --no-dev --no-editable

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "fastapi", "run", "--port", "8000"]
