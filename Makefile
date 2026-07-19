# PersonalClaw build & deploy targets

# The virtualenv lives INSIDE this repo at .venv/ (recreated here during the S12
# workspace split — venvs are not relocatable). PyInstaller output stays intra-repo
# too. Override VENV=... / PYI_BUNDLE_DIR=... if yours differ.
PYTHON  ?= python3
VENV    ?= .venv/bin
PKG     := src/personalclaw
TESTS   := tests

# Docker / Podman / Finch — the runtime is auto-detected (override with COMPOSE=...)
COMPOSE ?= $(shell \
	if command -v docker >/dev/null 2>&1; then echo "docker compose"; \
	elif command -v podman-compose >/dev/null 2>&1; then echo "podman-compose"; \
	elif command -v finch >/dev/null 2>&1; then echo "finch compose"; \
	else echo "docker compose"; fi)

COMPOSE_DIR  := deploy/compose
BASE_FILE    := -f $(COMPOSE_DIR)/compose.yaml
BUILD_OVERLAY:= $(BASE_FILE) -f $(COMPOSE_DIR)/compose.build.yaml
PROD_OVERLAY := $(BASE_FILE) -f $(COMPOSE_DIR)/compose.prod.yaml
DEV_OVERLAY  := $(BUILD_OVERLAY) -f $(COMPOSE_DIR)/compose.dev.yaml

WEB_DIR         := web
DESKTOP_DIR     := desktop
PYI_BUNDLE_DIR  := dist/personalclaw-backend

.PHONY: help format lint test build clean \
        serve serve-fresh serve-web \
        web-build backend-build pyinstaller \
        desktop desktop-dist \
        docker-build docker-up docker-down docker-logs docker-deploy \
        dev-up dev-down

## help: list available targets
help:
	@grep -E '^##' Makefile | sed 's/^## //'

# ── Python ─────────────────────────────────────────────────────────────────────

## format: auto-format source and tests with black + isort
format:
	$(PYTHON) -m black $(PKG) $(TESTS)
	$(PYTHON) -m isort $(PKG) $(TESTS)

## lint: check formatting, run flake8 and mypy
lint:
	$(PYTHON) -m black --check $(PKG) $(TESTS)
	$(PYTHON) -m isort --check-only $(PKG) $(TESTS)
	$(PYTHON) -m flake8 $(PKG) $(TESTS)
	$(PYTHON) -m mypy $(PKG)

## test: run pytest
test:
	$(PYTHON) -m pytest

## build: build a distributable wheel + sdist
build:
	$(PYTHON) -m build

## clean: remove build artifacts and caches
clean:
	rm -rf build/ dist/ *.egg-info src/*.egg-info
	rm -rf .pytest_cache .mypy_cache .hypothesis .coverage htmlcov
	rm -rf $(PKG)/static/dist $(WEB_DIR)/dist
	rm -rf $(DESKTOP_DIR)/dist $(DESKTOP_DIR)/backend-dist
	rm -f $(WEB_DIR)/tsconfig.tsbuildinfo
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name .DS_Store -delete 2>/dev/null || true

# ── Bare local dev servers (no container runtime, no desktop bundle) ───────────
# The simplest way to run + debug: plain processes you can curl, tail, and
# restart directly. PERSONALCLAW_HOME defaults to an isolated ./.dev-home so a
# dev run never touches your real ~/.personalclaw.

DEV_HOME      ?= $(CURDIR)/.dev-home
DEV_PORT      ?= 10000

## serve: run ONE gateway process that serves both the API and the built SPA on
## DEV_PORT (default 10000). Requires a built web/dist (run `make web-build` once,
## or `make serve-fresh`). Binds to the local network (0.0.0.0) and SKIPS token
## auth for local-network clients (loopback + RFC1918/link-local/ULA) via
## PERSONALCLAW_BYPASS_LOCAL_NETWORKS=1 — so any device on the dev LAN reaches the
## dashboard with no token. (This is the IP-gated bypass, NOT AUTH_MODE=none,
## which would force loopback-only; public/non-private origins still need a token.)
serve:
	PERSONALCLAW_HOME=$(DEV_HOME) PERSONALCLAW_WORKSPACE=$(DEV_HOME)/workspace \
		PERSONALCLAW_BIND_HOST=0.0.0.0 \
		PERSONALCLAW_BYPASS_LOCAL_NETWORKS=1 \
		$(VENV)/personalclaw gateway --no-open --port $(DEV_PORT) --json-ready

## serve-fresh: build the SPA, then `serve` (use after frontend changes).
serve-fresh: web-build serve

## serve-web: Vite dev server (HMR) on :3000, proxying /api + /ws to the gateway
## on DEV_PORT. Run `make serve` in another shell first. Best for frontend
## iteration; the gateway is the API/backend.
serve-web:
	cd $(WEB_DIR) && PERSONALCLAW_PORT=$(DEV_PORT) npm run dev

# ── Web app & PyInstaller bundle ───────────────────────────────────────────────

## web-build: compile the React SPA (web) into dist/ + link static/dist -> web/dist
##
## static/dist is a SYMLINK, not a copy: the gateway serves from
## $(PKG)/static/dist (dashboard/handlers/core.py _DIST_DIR) while Vite builds to
## $(WEB_DIR)/dist. Linking (not copying) keeps them a single source of truth so a
## later `npm run build` is live immediately — matching what the runtime resolver
## frontend.ensure_dev_dist_symlink() creates. A `cp -R` here would leave a frozen
## real directory that shadows the runtime symlink and silently serves a stale SPA.
# Install + build from the WORKSPACE ROOT (never `cd web`): the root package.json
# owns the npm workspace (web, desktop) and its single package-lock.json. Running
# npm inside a workspace member trips npm's optional-dependency bug (npm/cli#4828)
# and skips platform-native binaries (rollup/esbuild/lightningcss) → a broken build.
web-build:
	npm ci
	npm run build --workspace $(WEB_DIR)
	mkdir -p $(PKG)/static
	rm -rf $(PKG)/static/dist
	ln -s ../../../$(WEB_DIR)/dist $(PKG)/static/dist

## pyinstaller: build a standalone backend bundle in dist/personalclaw-backend/
pyinstaller: web-build
	$(VENV)/pyinstaller personalclaw-backend.spec --noconfirm

## backend-build: alias for `pyinstaller`
backend-build: pyinstaller

# ── Electron desktop app ───────────────────────────────────────────────────────

## desktop: refresh web + backend bundle and stage them for the desktop app
desktop: pyinstaller
	rm -rf $(DESKTOP_DIR)/backend-dist
	mkdir -p $(DESKTOP_DIR)/backend-dist
	cp -R $(PYI_BUNDLE_DIR) $(DESKTOP_DIR)/backend-dist/
	npm ci  # workspace root install (see web-build note); covers the desktop member

## desktop-dist: build a signed .dmg in desktop/dist/
desktop-dist: desktop
	cd $(DESKTOP_DIR) && npm run dist

# ── Container stack (runtime auto-detected; override e.g. COMPOSE="podman-compose") ──

## docker-build: build images locally from the working tree
docker-build:
	$(COMPOSE) $(BUILD_OVERLAY) build

## docker-up: start the stack in detached mode (uses published images by default)
docker-up:
	$(COMPOSE) $(BASE_FILE) up -d

## docker-down: stop and remove containers (named volumes preserved)
docker-down:
	$(COMPOSE) $(BASE_FILE) down

## docker-logs: tail logs from all containers
docker-logs:
	$(COMPOSE) $(BASE_FILE) logs -f

## docker-deploy: build images and bring up the prod stack in one shot
docker-deploy:
	$(COMPOSE) $(BUILD_OVERLAY) build
	$(COMPOSE) $(PROD_OVERLAY) up -d

# ── Dev compose stack (builds from the working tree + live source mount) ───────

## dev-up: build from the working tree and start the dev stack with live reload
dev-up:
	$(COMPOSE) $(DEV_OVERLAY) up -d --build

## dev-down: stop the dev stack
dev-down:
	$(COMPOSE) $(DEV_OVERLAY) down
