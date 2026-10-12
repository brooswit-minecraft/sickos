# packwiz modpack build.
#
# CI calls these same targets, so local and CI cannot drift.
# Requires Go (for `make tools`) or packwiz already on PATH.

# Pinned packwiz revision. packwiz publishes no tagged releases, so we pin a
# commit SHA. Bump deliberately, and re-run `make refresh` when you do.
PACKWIZ_REF ?= dfd8b68a4796c763e25bad50265ea1f1233e24f1

PACK_NAME    := $(shell sed -n 's/^name = "\(.*\)"/\1/p' pack.toml)
PACK_VERSION := $(shell sed -n 's/^version = "\(.*\)"/\1/p' pack.toml)
BUILD_DIR    := build
MRPACK       := $(BUILD_DIR)/$(PACK_NAME)-$(PACK_VERSION).mrpack

# Private, approved runtime dependency. Its JAR is materialized only for a
# build, checked against its immutable digest, and exported as an override.

# Prefer a packwiz already on PATH; otherwise use the one `make tools` installs.
PACKWIZ ?= $(shell command -v packwiz 2>/dev/null || echo $(CURDIR)/bin/packwiz)

.PHONY: all build refresh check verify-runtime tools clean

all: build

## tools: install the pinned packwiz into ./bin (needs Go).
tools:
	@mkdir -p bin
	GOBIN=$(CURDIR)/bin go install github.com/packwiz/packwiz@$(PACKWIZ_REF)

## refresh: rewrite index.toml from the files on disk.
refresh:
	$(PACKWIZ) refresh

## check: fail if `packwiz refresh` changes anything that is tracked in git.
check: refresh
	@if ! git diff --quiet -- pack.toml index.toml mods; then \
		echo "ERROR: 'packwiz refresh' produced a diff. Run 'make refresh' and commit the result."; \
		git --no-pager diff -- pack.toml index.toml mods; \
		exit 1; \
	fi
	@echo "OK: index is up to date."

## verify-runtime: prove the exported mrpack and a materialized server use the
## same NeoForge version accepted by Quark's own metadata.
verify-runtime: build
	python3 scripts/verify-neoforge-runtime.py --mrpack $(MRPACK)

## build: export the pack to build/$(PACK_NAME)-$(PACK_VERSION).mrpack.
build: refresh
	@mkdir -p $(BUILD_DIR)
	$(PACKWIZ) modrinth export -o $(MRPACK)
	@echo "Built $(MRPACK)"

clean:
	rm -rf $(BUILD_DIR) bin
