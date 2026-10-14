FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./backend/
COPY migrations/ ./migrations/
COPY alembic.ini ./
COPY gaia-io-generator-docs/ ./gaia-io-generator-docs/
COPY --from=frontend /build/dist ./frontend/dist
RUN mkdir /data && useradd --uid 10001 --create-home app && chown app:app /data
USER app
ENV DATA_DIR=/data PORT=8000 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 CMD ["python", "-m", "backend.healthcheck"]
CMD ["sh", "-c", "exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]
