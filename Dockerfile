FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-ara tesseract-ocr-eng && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml requirements.lock.txt ./
COPY platform_api ./platform_api
RUN pip install --no-cache-dir -c requirements.lock.txt .
COPY migrations ./migrations
COPY alembic.ini ./
COPY evals ./evals
COPY web ./web
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "platform_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
