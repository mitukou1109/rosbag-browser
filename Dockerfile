FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app

FROM base AS runtime
RUN pip install --no-cache-dir .

RUN useradd --create-home --shell /usr/sbin/nologin appuser

EXPOSE 8000
ENTRYPOINT ["python", "-m", "app.docker_entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS test
ENV PYTEST_ADDOPTS="-p no:cacheprovider"
RUN pip install --no-cache-dir ".[test]"
COPY tests ./tests

RUN useradd --create-home --shell /usr/sbin/nologin appuser

EXPOSE 8000
ENTRYPOINT ["python", "-m", "app.docker_entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
