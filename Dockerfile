FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy application code
COPY narad/ narad/

# Create data directory for SQLite
RUN mkdir -p /data

# Set environment variable for database path
ENV DATABASE_URL=sqlite+aiosqlite:////data/narad.db
ENV PORT=8000

EXPOSE 8000

CMD ["uvicorn", "narad.app:app", "--host", "0.0.0.0", "--port", "8000"]
