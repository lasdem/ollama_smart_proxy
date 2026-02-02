# Deployment Guide

This guide covers deploying the Ollama Smart Proxy using Docker.

## Prerequisites

- Docker (version 20.10 or higher)
- Docker Compose (version 1.29 or higher)
- Running Ollama instance (accessible from the Docker container)

## Quick Start

### 1. Configure Environment Variables

Copy the example environment file and customize it:

```bash
cp .env.example .env
```

Edit `.env` and adjust the settings:

```dotenv
# Point to your Ollama instance
OLLAMA_API_BASE=http://localhost:11434

# Or if Ollama is running in Docker on the same host:
# OLLAMA_API_BASE=http://host.docker.internal:11434

# Adjust VRAM based on your GPU
TOTAL_VRAM_MB=80000

# Database type (sqlite for single instance, postgres for production)
DB_TYPE=sqlite
```

### 2. Build and Run

Using Docker Compose (recommended):

```bash
docker-compose up -d
```

Or using Docker directly:

```bash
docker build -t ollama-smart-proxy .
docker run -d \
  --name ollama-smart-proxy \
  -p 8003:8003 \
  -v $(pwd)/db:/app/db \
  --env-file .env \
  ollama-smart-proxy
```

### 3. Verify Deployment

Check the health endpoint:

```bash
curl http://localhost:8003/proxy/health
```

View logs:

```bash
docker-compose logs -f smart-proxy
# or
docker logs -f ollama-smart-proxy
```

## Production Deployment

### Using PostgreSQL

For production deployments, use PostgreSQL instead of SQLite:

1. Update `.env`:

```dotenv
DB_TYPE=postgres
DB_HOST=your-postgres-host
DB_PORT=5432
DB_NAME=smart_proxy
DB_USER=your-db-user
DB_PASSWORD=your-db-password
```

2. The database schema will be automatically created on first run.

### Docker Compose with PostgreSQL

Create `docker-compose.prod.yml`:

```yaml
version: '3.8'

services:
  postgres:
    image: postgres:15-alpine
    container_name: ollama-proxy-db
    environment:
      POSTGRES_DB: smart_proxy
      POSTGRES_USER: proxy_user
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - ollama-network
    restart: unless-stopped

  smart-proxy:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: ollama-smart-proxy
    ports:
      - "${PROXY_PORT:-8003}:8003"
    environment:
      - OLLAMA_API_BASE=${OLLAMA_API_BASE}
      - DB_TYPE=postgres
      - DB_HOST=postgres
      - DB_PORT=5432
      - DB_NAME=smart_proxy
      - DB_USER=proxy_user
      - DB_PASSWORD=${DB_PASSWORD}
    depends_on:
      - postgres
    networks:
      - ollama-network
    restart: unless-stopped

volumes:
  postgres_data:

networks:
  ollama-network:
    driver: bridge
```

Run with:

```bash
docker-compose -f docker-compose.prod.yml up -d
```

## Configuration

### Environment Variables

All configuration is done via environment variables. See `.env.example` for a complete list.

Key variables:

- `OLLAMA_API_BASE`: URL of your Ollama instance
- `OLLAMA_MAX_PARALLEL`: Maximum parallel requests to Ollama
- `TOTAL_VRAM_MB`: Total VRAM available (MB)
- `PROXY_PORT`: Port for the proxy server (default: 8003)
- `LOG_FORMAT`: `json` or `human` (default: json for production)
- `LOG_LEVEL`: `DEBUG`, `INFO`, `WARNING`, `ERROR` (default: INFO)

### Persistent Data

The proxy stores data in the `./db` directory, which is mounted as a volume:

- SQLite database: `./db/smart_proxy.db`
- Fallback logs: `./db/fallback_logs/`

Ensure this directory has appropriate permissions:

```bash
mkdir -p db/fallback_logs
chmod 755 db
```

## Monitoring

### Health Checks

Docker health checks are configured automatically. Check container health:

```bash
docker ps
# Look for "healthy" status
```

### Logs

View structured JSON logs:

```bash
docker-compose logs -f smart-proxy | jq .
```

View only errors:

```bash
docker-compose logs smart-proxy | jq 'select(.level == "ERROR")'
```

### Analytics

Access analytics via the API (when implemented):

```bash
# Request count by model
curl http://localhost:8003/proxy/analytics/models

# Error rates
curl http://localhost:8003/proxy/analytics/errors

# Priority score distribution
curl http://localhost:8003/proxy/analytics/priority
```

## Troubleshooting

### Container won't start

Check logs:

```bash
docker-compose logs smart-proxy
```

Common issues:
- Ollama not accessible: Verify `OLLAMA_API_BASE` is correct
- Port conflict: Change `PROXY_PORT` in `.env`
- Permission denied on db: `chmod 755 db`

### Database errors

If using SQLite:
- Ensure `./db` directory is writable
- Check disk space

If using PostgreSQL:
- Verify database connection settings
- Ensure PostgreSQL container is running
- Check network connectivity between containers

### Performance issues

- Increase `OLLAMA_MAX_PARALLEL` if you have VRAM headroom
- Adjust priority weights in `.env`
- Check Ollama backend performance

## Updating

To update to a new version:

```bash
# Pull latest code
git pull

# Rebuild and restart
docker-compose down
docker-compose build
docker-compose up -d
```

Database migrations (if needed) will run automatically on startup.

## Backup

### SQLite Backup

```bash
# Stop the container
docker-compose down

# Backup the database
cp db/smart_proxy.db db/smart_proxy.db.backup-$(date +%Y%m%d)

# Restart
docker-compose up -d
```

### PostgreSQL Backup

```bash
docker exec ollama-proxy-db pg_dump -U proxy_user smart_proxy > backup-$(date +%Y%m%d).sql
```

## Security Considerations

- The proxy does not implement authentication - use a reverse proxy (nginx, Caddy) for authentication
- Use PostgreSQL with strong passwords for production
- Keep Docker images updated
- Consider using Docker secrets for sensitive environment variables in production
- Run containers with least privilege (non-root user)

## Next Steps

- Set up log aggregation (ELK, Loki, etc.)
- Configure metrics export (Prometheus)
- Set up automated backups
- Implement monitoring alerts
