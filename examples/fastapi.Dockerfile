FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system memory-app \
    && useradd --system --gid memory-app --home-dir /app memory-app

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY examples/fastapi_reference.py ./examples/fastapi_reference.py

RUN python -m pip install --no-cache-dir ".[fastapi]" \
    && chown -R memory-app:memory-app /app

USER memory-app

EXPOSE 8000

CMD ["uvicorn", "examples.fastapi_reference:app", "--host", "127.0.0.1", "--port", "8000", "--no-access-log"]
