FROM python:3.11-slim AS build-image
WORKDIR /app

RUN pip install --user uvicorn
COPY . /app
RUN pip install --user .

FROM python:3.11-slim
WORKDIR /app

RUN apt-get update && apt-get install -y git docker.io && apt-get clean

COPY --from=build-image /root/.local /root/.local

ENV PATH=/root/.local/bin:$PATH

ENV GAMES_PATH=/app/gamebattle
ENV NETWORK=gamebattle
ENV GOOGLE_APPLICATION_CREDENTIALS=/app/credentials.json

EXPOSE 8000
CMD ["uvicorn", "gamebattle_backend.api:launch_app", "--factory", "--host", "0.0.0.0"]
