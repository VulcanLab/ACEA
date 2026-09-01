FROM python:3.11-slim
WORKDIR /app

RUN pip install --no-cache-dir --timeout 120 \
    "fastapi>=0.111" \
    "uvicorn[standard]>=0.29" \
    "pydantic>=2.5" \
    "pydantic-settings>=2.2" \
    "litellm>=1.40"

# Copy the whole project (adapter + framework module + README + manifest).
# ASIS modifies these via bind-mount; image rebuild picks them up.
COPY . /app/

CMD ["uvicorn", "arena_adapter:app", "--host", "0.0.0.0", "--port", "9010"]
