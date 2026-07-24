.PHONY: dev up down build test test-integration clean logs ps

dev:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile dev up --build

up:
	docker compose up --build

down:
	docker compose --profile dev down

build:
	docker compose --profile dev build

test:
	pytest arena-core/tests/ target-ai/tests/ judge/tests/ evolution/tests/ adapters/tests/ tests/integration/ -v

test-integration:
	pytest tests/integration/ -v

clean:
	docker compose --profile dev down -v --rmi local
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .next -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true

logs:
	docker compose --profile dev logs -f

ps:
	docker compose --profile dev ps
