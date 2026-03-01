test:
	uv run pytest tests/unit -x -q

test-e2e:
	RUN_E2E_TESTS=1 uv run pytest tests/e2e/ -x -q

lint:
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

format:
	uv run ruff format src/ tests/

dev:
	npx wrangler d1 migrations apply DB --local
	uv run pywrangler dev

frontend-build:
	cd frontend && npm run build

frontend-lint:
	cd frontend && npm run lint

frontend-format-check:
	cd frontend && npm run format:check

frontend-test:
	cd frontend && npm test

frontend-check: frontend-lint frontend-format-check frontend-test frontend-build

check: lint test frontend-check

deploy-staging: check frontend-build
	npx wrangler d1 migrations apply tasche-staging-db --env staging --remote
	uv run pywrangler deploy --env staging

deploy-production: check frontend-build
	npx wrangler d1 migrations apply tasche-production-db --env production --remote
	uv run pywrangler deploy --env production
