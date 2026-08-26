# nodder — build, test, install.
#
# The project is pure standard library, so "build" means zipping the package
# into one executable file rather than resolving dependencies.

PYTHON  ?= python3
PREFIX  ?= $(HOME)/.local
BINDIR  := $(PREFIX)/bin
UNITDIR := $(HOME)/.config/systemd/user
BUILD   := build
TARGET  := $(BUILD)/nodder

.DEFAULT_GOAL := help
.PHONY: help check test build install uninstall run dry-run status service \
        service-stop clean

help: ## Show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk -F':.*?## ' '{printf "  \033[1m%-14s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "  Install to a different prefix with:  make install PREFIX=/usr/local"

check: ## Verify the runtime prerequisites are present
	@$(PYTHON) -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' \
	  || { echo "need python 3.10+, found $$($(PYTHON) -V)"; exit 1; }
	@command -v herdr >/dev/null \
	  || { echo "herdr not found on PATH — see https://herdr.dev"; exit 1; }
	@echo "ok: $$($(PYTHON) -V), $$(herdr --version)"

test: ## Run the test suite
	@$(PYTHON) -m unittest discover -s tests -t . -q

build: $(TARGET) ## Build the standalone executable into build/

$(TARGET): $(wildcard nodder/*.py)
	@mkdir -p $(BUILD)/pkg
	@cp -r nodder $(BUILD)/pkg/
	@$(PYTHON) -m zipapp $(BUILD)/pkg \
	  --main nodder.cli:main \
	  --python "/usr/bin/env python3" \
	  --output $(TARGET) \
	  --compress
	@rm -rf $(BUILD)/pkg
	@chmod +x $(TARGET)
	@echo "built $(TARGET) ($$(du -h $(TARGET) | cut -f1))"

install: check test build ## Install nodder to $(BINDIR)
	@mkdir -p $(BINDIR)
	@install -m 755 $(TARGET) $(BINDIR)/nodder
	@echo "installed $(BINDIR)/nodder"
	@command -v nodder >/dev/null \
	  || echo "note: $(BINDIR) is not on your PATH"

uninstall: ## Remove the installed executable and unit file
	@rm -f $(BINDIR)/nodder $(UNITDIR)/nodder.service
	@echo "removed $(BINDIR)/nodder"

run: ## Run from the source tree
	@$(PYTHON) -m nodder --verbose

dry-run: ## Run from the source tree, deciding but never pressing
	@$(PYTHON) -m nodder --verbose --dry-run

status: ## List agents and whether each would be watched
	@$(PYTHON) -m nodder --status

service: install ## Install and start the systemd user service
	@mkdir -p $(UNITDIR)
	@sed 's|@BINDIR@|$(BINDIR)|' packaging/nodder.service > $(UNITDIR)/nodder.service
	@systemctl --user daemon-reload
	@systemctl --user enable --now nodder.service
	@systemctl --user --no-pager status nodder.service | head -5

service-stop: ## Stop and disable the systemd user service
	@systemctl --user disable --now nodder.service 2>/dev/null || true
	@echo "stopped"

clean: ## Remove build artefacts
	@rm -rf $(BUILD) .coverage
	@find . -name __pycache__ -type d -prune -exec rm -rf {} +
	@echo "cleaned"
