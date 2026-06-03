.PHONY: test lint typecheck check cov

test:
	uv run pytest tests/ $(ARGS)

cov:
	uv run pytest tests/ --cov=src/icm_engine --cov-report=term-missing $(ARGS)

lint:
	uv run ruff check src/ tests/

typecheck:
	uv run mypy src/

check: lint typecheck test
