# GameBattle Backend Setup Guide

This guide will walk you through the process of setting up and running the GameBattle backend application.

## Prerequisites

Before you begin, ensure you have the following:

1. Docker and Docker Compose installed on your system
2. A Redis instance (can be local or remote)
3. Firebase credentials for authentication
4. A directory with game files

## Environment Variables

Set up the following environment variables:

- `GAMES_PATH`: Path to the directory containing game files
- `ENABLE_COMPETITION`: Set to "true" to enable competition mode
- `REPORT_WEBHOOK` (optional): URL for reporting webhooks
- `ADMIN_EMAILS` (optional): List of admin email addresses
- `REDIS_HOST` (optional, default: "localhost"): Redis host address
- `REDIS_PORT` (optional, default: 6379): Redis port
- `REDIS_DB` (optional, default: 0): Redis database number
- `REDIS_PASSWORD` (optional): Redis password
- `GOOGLE_APPLICATION_CREDENTIALS`: Path to your Firebase credentials file

## Setup Steps

1. Create a `docker-compose.yml` file in your project directory:
   ```yaml
   version: '3'
   services:
     gamebattle-backend:
       image: rizerphe/gamebattle-docker-manager
       ports:
         - "8000:8000"
       environment:
         - GAMES_PATH=/app/gamebattle
         - ENABLE_COMPETITION=true
         - REPORT_WEBHOOK=your_webhook_url
         - ADMIN_EMAILS=["admin1@example.com", "admin2@example.com"]
         - REDIS_HOST=redis
         - REDIS_PORT=6379
         - REDIS_PASSWORD=your_redis_password
         - GOOGLE_APPLICATION_CREDENTIALS=/app/credentials.json
       volumes:
         - /path/to/your/credentials.json:/app/credentials.json
         - /path/to/your/games:/app/gamebattle
         - /var/run/docker.sock:/var/run/docker.sock
         - /tmp:/tmp
       networks:
         - gamebattle-network

     redis:
       image: redis
       command: redis-server --requirepass ${REDIS_PASSWORD}
       environment:
         - REDIS_PASSWORD=your_redis_password
       networks:
         - gamebattle-network

   networks:
     gamebattle-network:
       name: gamebattle
   ```

   Replace the environment variable values and volume mount paths with your actual configuration.

   Note: To give the container access to the Docker socket, we've added a volume mount:
   ```
   - /var/run/docker.sock:/var/run/docker.sock
   ```
   This allows the container to communicate with the Docker daemon on the host machine.

   Additionally, we've mounted the /tmp directory:
   ```
   - /tmp:/tmp
   ```
   This is necessary because the application uses named pipes for inter-process communication, which are created in the /tmp directory. Mounting this directory ensures that these pipes are accessible both inside the container and on the host machine, allowing for proper communication between processes.

2. Run the Docker Compose setup:
   ```
   docker-compose up -d
   ```

   This command will pull the pre-built `rizerphe/gamebattle-docker-manager` image, create the `gamebattle` network, and start both the GameBattle backend and Redis services.

3. The application should now be running and accessible at `http://localhost:8000`.

## Local Development

For local development without Docker:

1. Install Poetry:
   ```
   pip install poetry
   ```

2. Install dependencies:
   ```
   poetry install
   ```

3. Run the application:
   ```
   poetry run uvicorn gamebattle_backend.api:launch_app --factory --host 0.0.0.0 --port 8000
   ```

Remember to set all required environment variables before running the application locally.

## Docker Network

The application uses a custom Docker network named "gamebattle". This network is automatically created by Docker Compose when you run `docker-compose up`. It allows the GameBattle backend and Redis services to communicate with each other.

If you need to create the network manually or connect other containers to it, you can use the following command:

## Docker Socket Access

By default, the Docker container does not have access to the Docker socket. We've added a volume mount in the `docker-compose.yml` file to give the container access to the Docker socket. This allows the container to interact with the Docker daemon on the host machine.

However, please note that giving a container access to the Docker socket can pose security risks, as it essentially gives the container full access to the Docker daemon. Only do this if it's absolutely necessary for your application, and make sure to implement proper security measures within your application to prevent misuse of this access.

The pre-built `rizerphe/gamebattle-docker-manager` image already includes the necessary Docker CLI, so you don't need to install it separately.
