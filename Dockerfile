# app python + modelos locales horneados.
# ADR-001: el dashboard vive en el repo nous-dashboard (imagen propia);
# esta imagen sirve solo API REST + MCP.
FROM python:3.12-slim AS app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    NOUS_MCP_TRANSPORT=http \
    NOUS_API_HOST=0.0.0.0 \
    NOUS_HF_OFFLINE=true

RUN pip install --no-cache-dir poetry==1.8.4

WORKDIR /app

COPY pyproject.toml poetry.lock* ./
RUN poetry install --only main --no-root

COPY src ./src
COPY alembic.ini ./

# Hornea embeddings + reranker en la imagen (caché HF en
# /root/.cache/huggingface): local-first real — el contenedor
# arranca y sirve sin tocar la red.
RUN NOUS_HF_OFFLINE=false python -c \
    "from src.mcp.tools import precalentar_modelos; precalentar_modelos()"

COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

EXPOSE 8720
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["python", "-m", "src.main"]
