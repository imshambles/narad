FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY narad/ narad/

RUN mkdir -p /data

ENV DATABASE_URL=sqlite+aiosqlite:////data/narad.db
ENV PORT=8000

EXPOSE 8000

# Health check so Docker knows if the app is alive
HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health', timeout=5)" || exit 1

CMD ["uvicorn", "narad.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
