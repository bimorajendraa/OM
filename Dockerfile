FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt

COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir --no-deps .

ENV PARTRISK_HOME=/app

COPY . .

RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health').read()"

CMD ["uvicorn", "partrisk.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
