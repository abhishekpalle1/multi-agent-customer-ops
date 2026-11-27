# Linux/Codespaces/CI task runner. Windows developers use tasks.ps1 (GNU Make not required).

UV_RUN := uv run --locked

.PHONY: help lock install migrate run lint format typecheck test test-unit test-live check

help:
	@echo "Targets: lock install migrate run lint format typecheck test test-unit test-live check"

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
