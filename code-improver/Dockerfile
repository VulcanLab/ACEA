FROM python:3.11-slim
WORKDIR /app
# patch (legacy), docker CLI + compose plugin (so we can run `docker compose up`
# against the host engine via the mounted /var/run/docker.sock)
RUN apt-get update && apt-get install -y --no-install-recommends \
        patch curl ca-certificates gnupg && \
    install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg | \
        gpg --dearmor -o /etc/apt/keyrings/docker.gpg && \
    chmod a+r /etc/apt/keyrings/docker.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian bookworm stable" \
        > /etc/apt/sources.list.d/docker.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin && \
    rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir \
    "fastapi>=0.111" "uvicorn[standard]>=0.29" \
    "pydantic>=2.5" "pydantic-settings>=2.2" \
    "asyncpg>=0.29" "redis[hiredis]>=5.0" \
    "httpx>=0.27" "litellm>=1.40" "docker>=7.0" "orjson>=3.9"
COPY src/ /app/
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8010"]
