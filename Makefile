# Makefile — Developer convenience targets for the AI SDLC Orchestrator.
#
# Prerequisites:
#   - Python 3.11+ with the package installed: pip install -e ".[dev]"
#   - Docker Engine >= 20.10 (for container-* targets)
#   - sudo access (for setup-network target on Linux)
#
# Quick start:
#   make build                           # build the Docker image
#   make setup-network                   # create orchestrator-net + iptables rules (Linux, sudo)
#   make orchestrate FEATURE="Add auth"  # run a containerized orchestration
#   make dry-run    FEATURE="Add auth"   # preview the docker run command (no container launched)

.PHONY: help build test test-unit test-integration lint \
        setup-network setup-network-dry-run setup-network-refresh \
        load-apparmor apparmor-persist \
        persist-network-rules \
        show-seccomp-path enable-container-mode \
        orchestrate dry-run \
        clean clean-containers clean-image clean-runs \
        image-scan validate-seccomp check-docker check-env status

# ── Defaults ──────────────────────────────────────────────────────────────────
IMAGE_NAME     := ai-sdlc-orchestrator
IMAGE_TAG      := latest
DOCKERFILE     := infra/docker/Dockerfile
SECCOMP        := infra/docker/seccomp-profile.json
APPARMOR       := infra/docker/apparmor-profile
ENV_FILE       ?= $(HOME)/.orchestrator.env
WORKSPACE      ?= ./workspace
CONFIG         ?= config/default.yaml
FEATURE        ?= ""

# ── Help ──────────────────────────────────────────────────────────────────────
help: ## Show this help message
	@echo ""
	@echo "AI SDLC Orchestrator — Make targets"
	@echo "────────────────────────────────────────────────────────────────"
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*?##/ { \
		printf "  \033[36m%-30s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@echo ""
	@echo "  FEATURE env var is required for 'orchestrate' and 'dry-run':"
	@echo "    make orchestrate FEATURE=\"Build a todo app\""
	@echo ""
	@echo "  Quick first-time setup (Linux):"
	@echo "    make build                             # 1. Build image"
	@echo "    make setup-network                     # 2. Create network + iptables rules"
	@echo "    make persist-network-rules             # 3. Persist rules across reboots"
	@echo "    make enable-container-mode             # 4. Show config changes needed"
	@echo "    make orchestrate FEATURE=\"Add auth\"  # 5. Run containerized orchestration"
	@echo ""

# ── Image build ───────────────────────────────────────────────────────────────
build: ## Build the Docker image (ai-sdlc-orchestrator:latest)
	@echo "==> Building Docker image..."
	bash infra/scripts/build-image.sh
	@echo ""
	@echo "Rebuild required after changes to src/orchestrator/ or pyproject.toml."

build-tag: ## Build with a custom tag: make build-tag TAG=v1.2.3
	bash infra/scripts/build-image.sh --tag "$(TAG)"

# ── Testing ───────────────────────────────────────────────────────────────────
test: test-unit ## Run all tests (alias for test-unit)

test-unit: ## Run unit tests (fast, no Docker required)
	pytest tests/ -v --tb=short --ignore=tests/integration -x

test-integration: ## Run integration tests (requires running services)
	pytest tests/integration/ -v --tb=short -x

test-coverage: ## Run tests with coverage report
	pytest tests/ --ignore=tests/integration \
		--cov=src/orchestrator \
		--cov-report=term-missing \
		--cov-report=html:htmlcov \
		-v
	@echo ""
	@echo "HTML coverage report: htmlcov/index.html"

# ── Linting / type checking ───────────────────────────────────────────────────
lint: ## Run ruff + pyright (install with: pip install ruff pyright)
	@command -v ruff   >/dev/null 2>&1 && ruff check src/ tests/ || echo "[skip] ruff not installed"
	@command -v pyright >/dev/null 2>&1 && pyright src/ || echo "[skip] pyright not installed"

# ── Network & security setup (Linux only) ─────────────────────────────────────
setup-network: ## Create orchestrator-net Docker network + iptables rules (Linux, requires sudo)
	@if [ "$$(uname -s)" != "Linux" ]; then \
		echo "WARNING: setup-network is for Linux only."; \
		echo "         On macOS Docker Desktop, containers use the 'bridge' network."; \
		echo "         Run orchestrate with: make orchestrate FEATURE=\"...\" NO_NETWORK_ISOLATION=1"; \
	else \
		sudo bash infra/scripts/setup-network-policy.sh; \
	fi

setup-network-dry-run: ## Preview the iptables rules without applying them
	bash infra/scripts/setup-network-policy.sh --dry-run

setup-network-refresh: ## Refresh Anthropic IP allowlist (re-run after CDN IP rotation)
	sudo bash infra/scripts/setup-network-policy.sh --refresh

load-apparmor: ## Load the AppArmor profile (Linux only, requires sudo)
	@if [ "$$(uname -s)" != "Linux" ]; then \
		echo "WARNING: AppArmor is Linux only. Skipping."; \
	else \
		sudo bash infra/scripts/load-apparmor-profile.sh; \
	fi

apparmor-persist: ## Persist AppArmor profile across reboots (Linux only, requires sudo)
	@if [ "$$(uname -s)" != "Linux" ]; then \
		echo "WARNING: AppArmor is Linux only. Skipping."; \
	else \
		sudo cp infra/docker/apparmor-profile /etc/apparmor.d/ai-sdlc-orchestrator && \
		sudo systemctl reload apparmor && \
		echo "OK: AppArmor profile copied to /etc/apparmor.d/ and reloaded (persists across reboots)"; \
	fi

persist-network-rules: ## Install systemd service to restore iptables rules on boot (Linux only, requires sudo)
	@if [ "$$(uname -s)" != "Linux" ]; then \
		echo "WARNING: iptables persistence is Linux only. Rules will not survive reboot on macOS."; \
		echo "         On macOS Docker Desktop, use the 'bridge' network (NO_NETWORK_ISOLATION=1)."; \
	else \
		sudo bash infra/scripts/install-iptables-restore-service.sh; \
	fi

persist-network-rules-dry-run: ## Preview the iptables persistence service without installing it
	bash infra/scripts/install-iptables-restore-service.sh --dry-run

# ── Seccomp path helpers ───────────────────────────────────────────────────────
show-seccomp-path: ## Print the absolute path to the seccomp profile for use in config/default.yaml
	@SECCOMP_ABS="$$(realpath $(SECCOMP) 2>/dev/null || echo '')"; \
	if [ -z "$$SECCOMP_ABS" ]; then \
		echo "ERROR: Could not resolve path to $(SECCOMP)"; \
		echo "       Run from the project root directory."; \
		exit 1; \
	fi; \
	echo ""; \
	echo "Add the following to config/default.yaml (or your local config override):"; \
	echo ""; \
	echo "  container:"; \
	echo "    seccomp_profile_path: $$SECCOMP_ABS"; \
	echo ""; \
	echo "Or export as an env var for one-off runs (if supported):"; \
	echo "  ORCHESTRATOR_SECCOMP_PATH=$$SECCOMP_ABS"

enable-container-mode: ## Show the config change needed to enable containerized orchestration
	@echo ""; \
	echo "To enable containerized orchestration, edit config/default.yaml:"; \
	echo ""; \
	echo "  container:"; \
	echo "    enabled: true"; \
	SECCOMP_ABS="$$(realpath $(SECCOMP) 2>/dev/null || echo '')"; \
	if [ -n "$$SECCOMP_ABS" ]; then \
		echo "    seccomp_profile_path: $$SECCOMP_ABS"; \
	fi; \
	echo "    # apparmor_profile: ai-sdlc-orchestrator   # Linux only — run 'make load-apparmor' first"; \
	echo ""; \
	echo "Then run an orchestration with:"; \
	echo "  make orchestrate FEATURE=\"Your feature description\""

# ── Containerized orchestration ───────────────────────────────────────────────
check-docker: ## Check Docker Engine is available and meets minimum version
	@docker version --format '{{.Server.Version}}' >/dev/null 2>&1 \
		|| (echo "ERROR: Docker is not running. Start Docker and retry." && exit 1)
	@echo "OK: Docker is running"

check-env: ## Check that the API key env file exists
	@if [ ! -f "$(ENV_FILE)" ]; then \
		echo "ERROR: Secrets file not found: $(ENV_FILE)"; \
		echo ""; \
		echo "Create it with:"; \
		echo "  echo 'ANTHROPIC_API_KEY=sk-ant-...' > $(ENV_FILE)"; \
		echo "  chmod 600 $(ENV_FILE)"; \
		exit 1; \
	fi
	@echo "OK: Secrets file found: $(ENV_FILE)"

orchestrate: check-docker check-env ## Run containerized orchestration: make orchestrate FEATURE="..."
	@if [ -z "$(FEATURE)" ]; then \
		echo "ERROR: FEATURE is required."; \
		echo "Usage: make orchestrate FEATURE=\"Build a todo app\""; \
		exit 1; \
	fi
	orchestrate-container \
		--feature-request "$(FEATURE)" \
		--env-file "$(ENV_FILE)" \
		--workspace "$(WORKSPACE)" \
		--config "$(CONFIG)" \
		$(if $(NO_NETWORK_ISOLATION),--no-network-isolation,)

dry-run: check-docker ## Preview docker run command without launching: make dry-run FEATURE="..."
	@if [ -z "$(FEATURE)" ]; then \
		echo "ERROR: FEATURE is required."; \
		echo "Usage: make dry-run FEATURE=\"Build a todo app\""; \
		exit 1; \
	fi
	orchestrate-container \
		--feature-request "$(FEATURE)" \
		--env-file "$(ENV_FILE)" \
		--workspace "$(WORKSPACE)" \
		--config "$(CONFIG)" \
		--dry-run

# ── Image security ────────────────────────────────────────────────────────────
image-scan: ## Scan the Docker image for CVEs with Trivy (install: brew install trivy)
	@command -v trivy >/dev/null 2>&1 \
		|| (echo "ERROR: trivy not installed. Install: https://aquasecurity.github.io/trivy/" && exit 1)
	trivy image \
		--severity CRITICAL,HIGH \
		--ignore-unfixed \
		$(IMAGE_NAME):$(IMAGE_TAG)

validate-seccomp: ## Validate seccomp-profile.json syntax and required deny rules
	python3 -c " \
import json, sys; \
profile = json.load(open('$(SECCOMP)')); \
denied = {n for r in profile.get('syscalls',[]) for n in r.get('names',[]) if r.get('action','').startswith('SCMP_ACT_ERR')}; \
required = {'ptrace','mount','kexec_load','bpf','init_module'}; \
missing = required - denied; \
sys.exit(f'MISSING rules: {missing}') if missing else print('OK: seccomp profile valid') \
"

# ── Cleanup ───────────────────────────────────────────────────────────────────
clean: clean-containers ## Remove stopped orchestrator containers and dangling images

clean-containers: ## Remove stopped containers named orchestrator-*
	@docker ps -aq --filter "name=orchestrator-" | xargs -r docker rm -f \
		&& echo "Removed orchestrator containers" \
		|| echo "No orchestrator containers to remove"

clean-image: ## Remove the orchestrator image (forces full rebuild)
	@docker rmi -f $(IMAGE_NAME):$(IMAGE_TAG) 2>/dev/null \
		&& echo "Removed $(IMAGE_NAME):$(IMAGE_TAG)" \
		|| echo "Image $(IMAGE_NAME):$(IMAGE_TAG) not found"

clean-runs: ## Remove all workspace/runs/* directories (local artifact cleanup)
	@if [ -d "$(WORKSPACE)/runs" ]; then \
		rm -rf $(WORKSPACE)/runs && echo "Removed $(WORKSPACE)/runs"; \
	else \
		echo "No runs directory to clean"; \
	fi

# ── Status ────────────────────────────────────────────────────────────────────
status: ## Show running orchestrator containers and latest image
	@echo "==> Running orchestrator containers:"
	@docker ps --filter "name=orchestrator-" --format "table {{.Names}}\t{{.Status}}\t{{.CreatedAt}}" \
		|| echo "  (none)"
	@echo ""
	@echo "==> Orchestrator images:"
	@docker images $(IMAGE_NAME) --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.CreatedAt}}" \
		|| echo "  (none)"
