FROM condaforge/miniforge3:26.5.3-0
COPY --from=ghcr.io/astral-sh/uv:0.11.31 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN conda create --yes --name planora python=3.12 && conda clean --all --yes
RUN uv export --frozen --no-dev --no-hashes --no-emit-project --output-file /tmp/yoyu-requirements.txt \
    && uv pip install --no-cache --python /opt/conda/envs/planora/bin/python --requirements /tmp/yoyu-requirements.txt \
    && rm /tmp/yoyu-requirements.txt
COPY backend/yoyu ./backend/yoyu
COPY vendor/planora/backend/planora ./vendor/planora/backend/planora
COPY skills ./skills
COPY alembic.ini ./
RUN useradd --uid 10001 --create-home yoyu && mkdir /data && chown yoyu:yoyu /data
USER yoyu
ENV PATH="/opt/conda/envs/planora/bin:$PATH" \
    CONDA_DEFAULT_ENV=planora \
    CONDA_PREFIX=/opt/conda/envs/planora \
    PYTHONPATH="/app/backend:/app/vendor/planora/backend" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    YOYU_DATA_DIR=/data
EXPOSE 8011
CMD ["python", "-m", "uvicorn", "yoyu.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8011"]
