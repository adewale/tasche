test:
	uv run pytest tests/unit -x -q

test-all:
	uv run pytest tests/ -x -q

lint:
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

format:
	uv run ruff format src/ tests/

dev:
	uv run pywrangler dev

deploy-staging:
	uv run pywrangler deploy --env staging

deploy-production:
	uv run pywrangler deploy --env production

frontend-lint:
	cd frontend && npm run lint

frontend-format-check:
	cd frontend && npm run format:check

frontend-test:
	cd frontend && npm test

frontend-check: frontend-lint frontend-format-check frontend-test

check: lint test frontend-check
