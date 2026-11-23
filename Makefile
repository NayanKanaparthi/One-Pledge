# OnePledge layer 1 developer targets (SPEC 2.3). Run from the repository root.

PYTHON ?= python3
PIP_FLAGS ?=
SEED ?= 20261117
PROFILE ?= full
FORCE ?=

# Installable packages, in dependency order: the test tools first, since every package's dev extra
# declares them (SPEC_DECISIONS 117).
PACKAGES := tools contracts/fakes datagen outside_records bank_agent
# Import name of each package, used for the per package coverage gates.
COVERAGE_PACKAGES := onepledge_contract_fakes onepledge_datagen onepledge_outside_records
# The bank agent gate covers its domain and application layers combined (SPEC 2.2).
BANK_CORE := */onepledge_bank_agent/domain/*,*/onepledge_bank_agent/application/*
COVERAGE_MIN := 85
# The bank agent's unit-only gate (SPEC_DECISIONS 63) keeps its data apart from the full run's.
BANK_UNIT_COVERAGE := COVERAGE_FILE=.coverage.bank-unit
# mypy runs once per package so that test helpers with the same file name do not collide.
MYPY := $(PYTHON) -m mypy --config-file pyproject.toml
TEST_PATHS := $(foreach p,$(PACKAGES),$(p)/tests)
DATA_DIR := var/data/$(PROFILE)-$(SEED)

.DEFAULT_GOAL := help
.PHONY: help setup lint format typecheck contracts-check test test-fast test-sweep ci data clean

help: ## List the targets with one line each
	@echo "OnePledge layer 1 targets:"
	@awk 'BEGIN {FS = ":.*## "} /^[a-z][a-z-]*:.*## / {printf "  %-16s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Install every layer 1 package in editable mode with its dev tools
	@for p in $(PACKAGES); do \
		echo "setup: installing $$p"; \
		$(PYTHON) -m pip install --quiet $(PIP_FLAGS) -e "$$p[dev]" || exit 1; \
	done
	@echo "setup: done"

lint: ## Check lint rules and formatting with ruff
	@echo "lint: ruff check"
	@$(PYTHON) -m ruff check .
	@echo "lint: ruff format --check"
	@$(PYTHON) -m ruff format --check .

format: ## Format the code and apply safe lint fixes
	@echo "format: ruff format"
	@$(PYTHON) -m ruff format .
	@echo "format: ruff check --fix"
	@$(PYTHON) -m ruff check --fix .

typecheck: ## Run mypy in strict mode over every source and test tree
	@echo "typecheck: contracts/fakes"
	@$(MYPY) contracts/fakes/src contracts/fakes/tests
	@echo "typecheck: tools"
	@$(MYPY) tools/src tools/tests
	@echo "typecheck: datagen"
	@$(MYPY) datagen/src datagen/tests
	@echo "typecheck: outside_records"
	@$(MYPY) outside_records/src outside_records/tests outside_records/migrations
	@echo "typecheck: bank_agent"
	@MYPYPATH=outside_records/src:contracts/fakes/src $(MYPY) bank_agent/src bank_agent/tests bank_agent/migrations

contracts-check: ## Validate the contracts, their examples and the test vectors
	@echo "contracts-check: onepledge-contracts check"
	@onepledge-contracts check --contracts-dir contracts

# The unit-only gate: `-m unit` (no PostgreSQL, network or child process), at least COVERAGE_MIN% of the
# bank agent's domain and application with branches on. $(1) is the target name used in its lines.
define bank_unit_gate
	@echo "$(1): unit only pytest -m unit for onepledge_bank_agent with coverage"
	@$(BANK_UNIT_COVERAGE) $(PYTHON) -m coverage erase
	@$(BANK_UNIT_COVERAGE) $(PYTHON) -m pytest bank_agent/tests -q -m unit \
		--cov=onepledge_bank_agent --cov-branch --cov-report=
	@echo "$(1): unit only coverage gate for onepledge_bank_agent domain and application (minimum $(COVERAGE_MIN)%)"
	@$(BANK_UNIT_COVERAGE) $(PYTHON) -m coverage report --include="$(BANK_CORE)" --fail-under=$(COVERAGE_MIN) \
		--format=total | awk '{print "$(1): onepledge_bank_agent domain and application unit only coverage " $$0 "%"}'
	@$(BANK_UNIT_COVERAGE) $(PYTHON) -m coverage report --include="$(BANK_CORE)" --fail-under=$(COVERAGE_MIN) \
		> /dev/null || exit 1
endef

test: ## Run every test of every package with its coverage gate, then the bank unit gate
	@echo "test: pytest with coverage"
	@$(PYTHON) -m coverage erase
	@$(PYTHON) -m pytest $(TEST_PATHS) -q $(foreach m,$(COVERAGE_PACKAGES),--cov=$(m)) \
		--cov=onepledge_bank_agent --cov-branch --cov-report=
	@echo "test: coverage gate for onepledge_bank_agent domain and application (minimum $(COVERAGE_MIN)%)"
	@$(PYTHON) -m coverage report --include="$(BANK_CORE)" --fail-under=$(COVERAGE_MIN) --format=total \
		| awk '{print "test: onepledge_bank_agent domain and application coverage " $$0 "%"}'
	@$(PYTHON) -m coverage report --include="$(BANK_CORE)" --fail-under=$(COVERAGE_MIN) > /dev/null || exit 1
	@for m in $(COVERAGE_PACKAGES); do \
		echo "test: coverage gate for $$m (minimum $(COVERAGE_MIN)%)"; \
		$(PYTHON) -m coverage report --include="*/$$m/*" --fail-under=$(COVERAGE_MIN) --format=total \
			| awk -v m="$$m" '{print "test: " m " coverage " $$0 "%"}'; \
		$(PYTHON) -m coverage report --include="*/$$m/*" --fail-under=$(COVERAGE_MIN) > /dev/null || exit 1; \
	done
	$(call bank_unit_gate,test)

test-fast: ## Run only the unit and contract tests, then the bank unit coverage gate
	@echo "test-fast: pytest -m 'unit or contract'"
	@$(PYTHON) -m pytest $(TEST_PATHS) -q -m "unit or contract"
	$(call bank_unit_gate,test-fast)

test-sweep: ## Drop every optest_ scratch database and role on the test server, whatever its age
	@$(PYTHON) -m onepledge_testtools.scratch_databases

ci: ## Run lint, typecheck, contracts-check and test, stopping at the first failure
	@$(MAKE) --no-print-directory lint
	@$(MAKE) --no-print-directory typecheck
	@$(MAKE) --no-print-directory contracts-check
	@$(MAKE) --no-print-directory test

data: ## Generate the data set for SEED and PROFILE into var/data (FORCE=1 regenerates)
	@if [ -z "$(FORCE)" ] && [ -f "$(DATA_DIR)/checksums.json" ] \
		&& onepledge-datagen verify --data-dir "$(DATA_DIR)" > /dev/null 2>&1; then \
		echo "data: $(DATA_DIR) already holds a complete data set whose checksums verify; skipping (FORCE=1 regenerates)"; \
	else \
		echo "data: generating profile $(PROFILE) with seed $(SEED) into $(DATA_DIR)"; \
		onepledge-datagen generate --seed "$(SEED)" --profile "$(PROFILE)" --out "$(DATA_DIR)" --force; \
	fi

clean: ## Remove var/, caches and build artefacts (databases are not touched)
	@echo "clean: removing var/, caches and build artefacts"
	@rm -rf var .mypy_cache .ruff_cache .pytest_cache .hypothesis .coverage .coverage.* build dist
	@find . -path ./.git -prune -o \( -name "__pycache__" -o -name "*.egg-info" \) -type d -print \
		| xargs rm -rf
