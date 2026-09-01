.PHONY: dev up down build test test-unit test-integration clean logs ps

dev:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile dev up --build

up:
	docker compose up --build

down:
	docker compose --profile dev down

build:
	docker compose --profile dev build

# Every suite listed here must exist: pytest aborts the whole run on a missing
# path, which silently turns this target into a no-op. Add each suite here as it
# gets written, and keep the list in sync with the un-ignore rules in .gitignore.
UNIT_SUITES := arena-core/tests/ target-ai/tests/ judge/tests/ evolution/tests/ \
               adapters/tests/ report-composer/tests/ \
               target/red/acea-default-red/tests/

test:
	pytest $(UNIT_SUITES) tests/integration/ -v

test-unit:
	pytest $(UNIT_SUITES) -v

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
