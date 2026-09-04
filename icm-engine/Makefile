.PHONY: test lint typecheck check cov

test:
	uv run pytest tests/ $(ARGS)

cov:
	uv run pytest tests/ --cov=src/icm_engine --cov-report=term-missing $(ARGS)

lint:
	uv run ruff check src/ tests/

# CI runs on Linux; the desktop app targets Windows. Check both explicitly,
# so platform-guarded code can't pass on the developer's OS and fail in CI.
typecheck:
	uv run mypy --platform linux src/
	uv run mypy --platform win32 src/

check: lint typecheck test
