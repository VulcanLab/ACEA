#!/bin/sh
set -e

MINIO_ENDPOINT="${MINIO_ENDPOINT:-minio:9000}"
MINIO_ACCESS_KEY="${MINIO_ACCESS_KEY:-arena}"
MINIO_SECRET_KEY="${MINIO_SECRET_KEY:-change-this-secret}"
BUCKET_REPLAYS="${MINIO_BUCKET_REPLAYS:-arena-replays}"
BUCKET_REPORTS="${MINIO_BUCKET_REPORTS:-arena-reports}"

echo "Waiting for MinIO to be ready..."
until mc alias set arena http://${MINIO_ENDPOINT} ${MINIO_ACCESS_KEY} ${MINIO_SECRET_KEY}; do
    sleep 2
done

echo "Creating buckets..."
mc mb --ignore-existing arena/${BUCKET_REPLAYS}
mc mb --ignore-existing arena/${BUCKET_REPORTS}

echo "Setting bucket policies..."
mc anonymous set download arena/${BUCKET_REPORTS}

echo "MinIO initialization complete."
