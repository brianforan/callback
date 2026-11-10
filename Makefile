.DEFAULT_GOAL := help
PROD := docker compose -f docker-compose.yml -f docker-compose.prod.yml
DEV := docker compose -f docker-compose.yml -f docker-compose.dev.yml

-include .env
HTTP_BIND ?= 127.0.0.1:8080
CALLBACK_API_URL ?= http://localhost:$(lastword $(subst :, ,$(HTTP_BIND)))
# The host's Ansible deploy sets COMPOSE_FILE in a public host's .env, so every docker compose
# command there, make up included, uses the production files.
ON_PROD_HOST := $(findstring docker-compose.prod.yml,$(COMPOSE_FILE))

.PHONY: help init up dev web down logs user test token postings resumes prod-up

help: ## Show available commands
	@grep -hE '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

init: ## Create .env with generated secrets and the first free port from 8080 (keeps an existing one)
	@test -f .env || { umask 077; port=8080; \
		while lsof -nP -iTCP:$$port -sTCP:LISTEN >/dev/null 2>&1; do port=$$((port + 1)); done; \
		sed \
		-e "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$$(openssl rand -hex 24)/" \
		-e "s/^SECRET_KEY=.*/SECRET_KEY=$$(openssl rand -hex 32)/" \
		-e "s/^HTTP_BIND=.*/HTTP_BIND=127.0.0.1:$$port/" \
		.env.example > .env; echo "Created .env using port $$port"; }

up: init ## Build and start everything, then print the URL (on a public host, behind Caddy)
	docker compose up -d --build --remove-orphans
ifeq ($(ON_PROD_HOST),)
	@echo "Callback is at http://localhost:$$(sed -n 's/^HTTP_BIND=.*://p' .env)"
else
	@echo "Callback is up on $$(sed -n 's/^HTTP_BIND=//p' .env), behind the host's Caddy."
endif

dev: init ## Start with the API reloading whenever a file in backend/app changes
ifneq ($(ON_PROD_HOST),)
	@echo "This is a public host, and make dev would drop the production settings here. Use make up." >&2; exit 1
endif
	$(DEV) up -d --build --remove-orphans
	@echo "API reloads on save. Run make web for the frontend, and make up to go back."

web: ## Run the frontend with hot reload at http://localhost:5173, using the running stack's API
	@test -d frontend/node_modules || npm --prefix frontend install
	cd frontend && VITE_API_PROXY=$(CALLBACK_API_URL) npm run dev

down: ## Stop everything (data stays in the pgdata volume)
	docker compose down

logs: ## Follow logs from all services
	docker compose logs -f

user: ## Create your login: make user EMAIL=you@example.com
	@test -n "$(EMAIL)" || { echo "Usage: make user EMAIL=you@example.com"; exit 1; }
	docker compose exec api python -m app.cli create-user --email "$(EMAIL)"

test: ## Run the backend, tailor, compiler and frontend test suites in containers
	docker build --target test -t callback-api-test backend
	docker run --rm callback-api-test
	docker build --target test -t callback-tailor-test tailor
	docker run --rm --network none callback-tailor-test
	docker build --target test -t callback-compiler-test compiler
	docker run --rm --network none callback-compiler-test
	docker build --target test -t callback-web-test frontend
	docker run --rm --network none callback-web-test

token: ## Save a new API token for logging applications over HTTP: make token EMAIL=you@example.com
	@test -n "$(EMAIL)" || { echo "Usage: make token EMAIL=you@example.com"; exit 1; }
	@mkdir -p "$(HOME)/.config/callback"
	@umask 077; token=$$(docker compose exec -T api python -m app.cli create-token --email "$(EMAIL)" --name intake) \
		&& printf 'CALLBACK_API_URL=%s\nCALLBACK_API_TOKEN=%s\n' "$(CALLBACK_API_URL)" "$$token" > "$(HOME)/.config/callback/env" \
		&& echo "Token saved to ~/.config/callback/env"

postings: ## Create missing postings from applications and re-parse all: make postings EMAIL=you@example.com
	@test -n "$(EMAIL)" || { echo "Usage: make postings EMAIL=you@example.com"; exit 1; }
	docker compose exec api python -m app.cli sync-postings --email "$(EMAIL)"

resumes: ## Re-extract stored resume text after changing the LaTeX rules: make resumes EMAIL=you@example.com
	@test -n "$(EMAIL)" || { echo "Usage: make resumes EMAIL=you@example.com"; exit 1; }
	docker compose exec api python -m app.cli refresh-resumes --email "$(EMAIL)"

prod-up: init ## Start with the production settings, the same as make up on a public host
	$(PROD) up -d --build --remove-orphans
