test:
	uv run pytest tests/unit -x -q

# Restores auth on staging: deletes DISABLE_AUTH, then verifies the API
# actually returns 401 again.  Exits non-zero (loudly) if staging is left
# open — that must never be silently ignored.
restore-staging-auth:
	@echo "Restoring auth on staging..."
	@yes | npx wrangler secret delete DISABLE_AUTH --env staging || \
		{ echo "ERROR: failed to delete DISABLE_AUTH — staging may still have auth disabled!"; \
		  echo "Run: npx wrangler secret delete DISABLE_AUTH --env staging"; exit 1; }
	@for i in 1 2 3 4 5 6 7 8 9 10; do \
		STATUS=$$(curl -s -o /dev/null -w '%{http_code}' https://tasche-staging.adewale-883.workers.dev/api/articles); \
		if [ "$$STATUS" = "401" ]; then echo "Auth restored (attempt $$i)"; exit 0; fi; \
		echo "  Waiting for auth restore... (attempt $$i, got $$STATUS)"; \
		sleep 3; \
	done; \
	echo "ERROR: staging still not returning 401 — auth may still be disabled!"; \
	echo "Run: npx wrangler secret delete DISABLE_AUTH --env staging"; \
	exit 1

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
	@trap '$(MAKE) restore-staging-auth; exit 130' INT TERM; \
		RUN_E2E_TESTS=1 uv run pytest tests/e2e/ -x -q; \
		TEST_EXIT=$$?; \
		trap - INT TERM; \
		$(MAKE) restore-staging-auth || exit 1; \
		exit $$TEST_EXIT

lint:
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

format:
	uv run ruff format src/ tests/

setup:
	@test -f .dev.vars || (cp .dev.vars.example .dev.vars && echo "DISABLE_AUTH=true" >> .dev.vars && echo "Created .dev.vars with auth disabled for local dev")
	@grep -q "^WORKER_ENV=" .dev.vars || (echo "WORKER_ENV=development" >> .dev.vars && echo "Added WORKER_ENV=development to .dev.vars (required for DISABLE_AUTH)")
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
	@trap '$(MAKE) restore-staging-auth; exit 130' INT TERM; \
		RUN_E2E_TESTS=1 uv run pytest tests/e2e/ -x -q; \
		PYTEST_EXIT=$$?; \
		(cd frontend && E2E_BASE_URL=https://tasche-staging.adewale-883.workers.dev npx playwright test); \
		PW_EXIT=$$?; \
		trap - INT TERM; \
		$(MAKE) restore-staging-auth || exit 1; \
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
