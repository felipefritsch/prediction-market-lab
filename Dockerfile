FROM python:3.12-slim
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.11.2 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY config ./config
RUN uv sync --locked --no-dev --no-editable
RUN useradd --create-home --uid 10001 lab && mkdir -p /app/data && chown -R lab:lab /app/data
USER lab
ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT ["pml"]
CMD ["doctor"]
