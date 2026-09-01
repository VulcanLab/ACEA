FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir \
    "fastapi>=0.111" "uvicorn[standard]>=0.29" \
    "pydantic>=2.5" "pydantic-settings>=2.2" \
    "httpx>=0.27" "litellm>=1.40" "orjson>=3.9"
COPY src/ /app/
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8010"]
