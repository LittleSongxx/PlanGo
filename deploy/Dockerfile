FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.31 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --no-cache --locked --no-dev --no-install-project --python /usr/local/bin/python --no-python-downloads
COPY backend/yoyu ./backend/yoyu
COPY vendor/planora/backend/planora ./vendor/planora/backend/planora
COPY skills ./skills
COPY alembic.ini ./
RUN useradd --uid 10001 --create-home yoyu && mkdir /data && chown yoyu:yoyu /data
USER yoyu
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/backend:/app/vendor/planora/backend" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    YOYU_DATA_DIR=/data
EXPOSE 8011
CMD ["python", "-m", "uvicorn", "yoyu.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8011"]
