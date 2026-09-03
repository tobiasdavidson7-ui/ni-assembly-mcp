# syntax=docker/dockerfile:1
#
# Image for the streamable-HTTP transport (`ni-assembly-mcp serve --http`) and for
# the offline index builder (`ni-assembly-mcp index hansard`). The default local
# setup is stdio and needs no container -- see README "Configuring an MCP client".
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NI_ASSEMBLY_MCP_INDEX_DB_PATH=/data/index/index.db \
    NI_ASSEMBLY_MCP_HISHEL_CACHE_DIR=/data/http-cache

WORKDIR /app

# python:3.12-slim bundles SQLite with FTS5 enabled -- required by the Hansard index.
COPY pyproject.toml README.md ./
COPY ni_assembly_mcp ./ni_assembly_mcp
RUN pip install --no-cache-dir .

# Persistent state lives under /data (see docker-compose.yaml volumes).
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /data/index /data/http-cache \
    && chown -R app:app /data
USER app

EXPOSE 8000

ENTRYPOINT ["ni-assembly-mcp"]
CMD ["serve", "--http", "--host", "0.0.0.0", "--port", "8000"]
