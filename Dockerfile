FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

COPY fenix_api.py .
ENV PORT=8000
CMD uvicorn fenix_api:app --host 0.0.0.0 --port ${PORT}
