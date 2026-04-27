test:
	uv run pytest tests/unit -x -q

test-e2e:
	@echo "Enabling auth bypass on staging..."
	@echo "true" | npx wrangler secret put DISABLE_AUTH --env staging
	@echo "Waiting for secret propagation..."
	@for i in 1 2 3 4 5 6 7 8 9 10; do \
		STATUS=$$(curl -s -o /dev/null -w '%{http_code}' https://tasche-staging.adewale-883.workers.dev/api/articles); \
		if [ "$$STATUS" != "401" ]; then echo "Auth bypass active (attempt $$i)"; break; fi; \
		echo "  Waiting... (attempt $$i, got $$STATUS)"; \
		sleep 3; \
	done
	@echo "Running E2E tests..."
	@RUN_E2E_TESTS=1 uv run pytest tests/e2e/ -x -q; \
		EXIT_CODE=$$?; \
		echo "Restoring auth on staging..."; \
		yes | npx wrangler secret delete DISABLE_AUTH --env staging 2>/dev/null; \
		exit $$EXIT_CODE

lint:
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

typecheck:
	PYTHONPATH=src uvx ty check src/boundary scripts/bundle_size.py

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

bundle-size:
	uv run python scripts/bundle_size.py

check: lint typecheck test frontend-check

smoke-staging:
	@python3 scripts/smoke-test.py https://tasche-staging.adewale-883.workers.dev

smoke-production:
	@python3 scripts/smoke-test.py https://tasche-production.adewale-883.workers.dev

verify-staging: smoke-staging
	@echo "Enabling auth bypass on staging..."
	@echo "true" | npx wrangler secret put DISABLE_AUTH --env staging
	@echo "Waiting for secret propagation..."
	@for i in 1 2 3 4 5 6 7 8 9 10; do \
		STATUS=$$(curl -s -o /dev/null -w '%{http_code}' https://tasche-staging.adewale-883.workers.dev/api/articles); \
		if [ "$$STATUS" != "401" ]; then echo "Auth bypass active (attempt $$i)"; break; fi; \
		echo "  Waiting... (attempt $$i, got $$STATUS)"; \
		sleep 3; \
	done
	@echo "Running E2E + Playwright tests..."
	@RUN_E2E_TESTS=1 uv run pytest tests/e2e/ -x -q; \
		PYTEST_EXIT=$$?; \
		cd frontend && E2E_BASE_URL=https://tasche-staging.adewale-883.workers.dev npx playwright test; \
		PW_EXIT=$$?; \
		echo "Restoring auth on staging..."; \
		yes | npx wrangler secret delete DISABLE_AUTH --env staging 2>/dev/null; \
		if [ $$PYTEST_EXIT -ne 0 ]; then exit $$PYTEST_EXIT; fi; \
		exit $$PW_EXIT

deploy-staging: check frontend-build
	npx wrangler d1 migrations apply tasche-staging-db --env staging --remote
	uv run pywrangler deploy --env staging
	$(MAKE) smoke-staging

deploy-production: check frontend-build
	npx wrangler d1 migrations apply tasche-production-db --env production --remote
	uv run pywrangler deploy --env production
	$(MAKE) smoke-production
