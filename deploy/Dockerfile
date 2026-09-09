FROM condaforge/miniforge3:26.5.3-0
COPY --from=ghcr.io/astral-sh/uv:0.11.31 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN conda create --yes --name plango python=3.12 && conda clean --all --yes
RUN uv export --frozen --no-dev --no-hashes --no-emit-project --output-file /tmp/plango-requirements.txt \
    && uv pip install --no-cache --python /opt/conda/envs/plango/bin/python --requirements /tmp/plango-requirements.txt \
    && rm /tmp/plango-requirements.txt
COPY README.md ./
COPY backend/plango ./backend/plango
COPY vendor/plango_harness/backend/plango_harness ./vendor/plango_harness/backend/plango_harness
COPY skills ./skills
COPY alembic.ini ./
RUN chmod -R a+rX /app && useradd --uid 10001 --create-home plango && mkdir /data && chown plango:plango /data
USER plango
ENV PATH="/opt/conda/envs/plango/bin:$PATH" \
    CONDA_DEFAULT_ENV=plango \
    CONDA_PREFIX=/opt/conda/envs/plango \
    PYTHONPATH="/app/backend:/app/vendor/plango_harness/backend" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLANGO_DATA_DIR=/data
EXPOSE 8011
CMD ["python", "-m", "uvicorn", "plango.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8011"]
