test:
	uv run pytest tests/unit -x -q

test-e2e:
	RUN_E2E_TESTS=1 uv run pytest tests/e2e/ -x -q

lint:
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

format:
	uv run ruff format src/ tests/

setup:
	@test -f .dev.vars || (cp .dev.vars.example .dev.vars && echo "DISABLE_AUTH=true" >> .dev.vars && echo "Created .dev.vars with auth disabled for local dev")
	uv sync
	cd frontend && npm install

dev: setup frontend-build
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

smoke-staging:
	@python3 scripts/smoke-test.py https://tasche-staging.adewale-883.workers.dev

smoke-production:
	@python3 scripts/smoke-test.py https://tasche-production.adewale-883.workers.dev

verify-staging: smoke-staging
	RUN_E2E_TESTS=1 uv run pytest tests/e2e/ -x -q
	cd frontend && E2E_BASE_URL=https://tasche-staging.adewale-883.workers.dev npx playwright test

deploy-staging: check frontend-build
	npx wrangler d1 migrations apply tasche-staging-db --env staging --remote
	uv run pywrangler deploy --env staging
	$(MAKE) smoke-staging

deploy-production: check frontend-build
	npx wrangler d1 migrations apply tasche-production-db --env production --remote
	uv run pywrangler deploy --env production
	$(MAKE) smoke-production
