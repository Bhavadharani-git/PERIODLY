FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    PERIODLY_FRONTEND_DIR=/app/frontend \
    TMPDIR=/tmp

RUN addgroup --system --gid 10001 periodly \
    && adduser --system --uid 10001 --ingroup periodly periodly

WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r /app/backend/requirements.txt

COPY --chown=periodly:periodly backend/app /app/backend/app
COPY --chown=periodly:periodly backend/alembic.ini /app/backend/alembic.ini
COPY --chown=periodly:periodly backend/migrations /app/backend/migrations
COPY --chown=periodly:periodly frontend /app/frontend

WORKDIR /app/backend
USER 10001:10001
EXPOSE 8000
CMD ["sh", "-c", "python -m alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"]
