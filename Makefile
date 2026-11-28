# Linux/Codespaces/CI task runner. Windows developers use tasks.ps1 (GNU Make not required).

UV_RUN := uv run --locked

.PHONY: help lock install migrate run lint format typecheck test test-unit test-live check \
	synthetic-validate synthetic-manifest seed-dev reset-seed-dev seed-test reset-seed-test

help:
	@echo "Targets: lock install migrate run lint format typecheck test test-unit test-live check"
	@echo "Synthetic data: synthetic-validate synthetic-manifest seed-dev reset-seed-dev seed-test reset-seed-test"
	@echo "  reset-seed-* are destructive and need ALLOW_DEV_DATABASE_RESET / ALLOW_TEST_DATABASE_RESET=true"

lock:
	uv lock

install:
	uv sync --locked

migrate:
	$(UV_RUN) alembic upgrade head

run:
	$(UV_RUN) uvicorn support_ops.api.main:app --reload --host 127.0.0.1 --port 8000

lint:
	$(UV_RUN) ruff check .
	$(UV_RUN) ruff format --check .

format:
	$(UV_RUN) ruff format .
	$(UV_RUN) ruff check --fix .

typecheck:
	$(UV_RUN) mypy

test:
	APP_ENV=test $(UV_RUN) pytest -m "not live_llm"

test-unit:
	$(UV_RUN) pytest tests/unit -m "not live_llm"

test-live:
	$(UV_RUN) pytest -m live_llm -rA

check: lint typecheck test

# Synthetic data (dataset p2-v1). Never run by check/test/migrate. reset-seed-* are
# destructive and additionally require ALLOW_DEV_DATABASE_RESET / ALLOW_TEST_DATABASE_RESET.
synthetic-validate:
	$(UV_RUN) python -m support_ops.synthetic validate

synthetic-manifest:
	$(UV_RUN) python -m support_ops.synthetic manifest

seed-dev:
	$(UV_RUN) python -m support_ops.synthetic seed --target development

reset-seed-dev:
	$(UV_RUN) python -m support_ops.synthetic reset-seed --target development

seed-test:
	APP_ENV=test $(UV_RUN) python -m support_ops.synthetic seed --target test

reset-seed-test:
	APP_ENV=test $(UV_RUN) python -m support_ops.synthetic reset-seed --target test
