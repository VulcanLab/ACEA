FROM python:3.11-slim AS base
WORKDIR /app
COPY pyproject.toml .
RUN mkdir -p src && pip install --no-cache-dir -e .

FROM base AS development
RUN pip install --no-cache-dir watchfiles
COPY src/ .

FROM base AS production
COPY src/ .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9002"]
