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
MAGICAVOXEL_LOADER_REPO   := brooswit-minecraft/magicavoxel-loader
MAGICAVOXEL_LOADER_TAG    := v0.1.2
MAGICAVOXEL_LOADER_FILE   := mods/magicavoxel-loader-0.1.2.jar
MAGICAVOXEL_LOADER_SHA256 := 65c2026c00bd4a799c0acbf1d90c1ad4d69233510a2399a37d052fddf0c48578

# Prefer a packwiz already on PATH; otherwise use the one `make tools` installs.
PACKWIZ ?= $(shell command -v packwiz 2>/dev/null || echo $(CURDIR)/bin/packwiz)

.PHONY: all build refresh check verify-runtime tools clean materialize-magicavoxel-loader verify-magicavoxel-loader-override

all: build

## tools: install the pinned packwiz into ./bin (needs Go).
tools:
	@mkdir -p bin
	GOBIN=$(CURDIR)/bin go install github.com/packwiz/packwiz@$(PACKWIZ_REF)

## refresh: rewrite index.toml from the files on disk.
refresh: materialize-magicavoxel-loader
	$(PACKWIZ) refresh

materialize-magicavoxel-loader:
	@mkdir -p mods
	@if [ ! -f "$(MAGICAVOXEL_LOADER_FILE)" ]; then \
		GH_TOKEN="$${GH_TOKEN:-$${GITHUB_TOKEN:-}}" gh release download "$(MAGICAVOXEL_LOADER_TAG)" \
			--repo "$(MAGICAVOXEL_LOADER_REPO)" --pattern "$(notdir $(MAGICAVOXEL_LOADER_FILE))" --dir mods --clobber; \
	fi
	@printf '%s  %s\n' "$(MAGICAVOXEL_LOADER_SHA256)" "$(MAGICAVOXEL_LOADER_FILE)" | sha256sum --check

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
	@$(MAKE) --no-print-directory verify-magicavoxel-loader-override
	@echo "Built $(MRPACK)"

verify-magicavoxel-loader-override:
	@set -eu; \
	entries=$$(unzip -Z1 "$(MRPACK)" | grep -cx "overrides/$(MAGICAVOXEL_LOADER_FILE)" || true); \
	test "$$entries" = 1; \
	workdir=$$(mktemp -d); trap 'rm -rf "$$workdir"' EXIT; \
	for install in client server; do \
		mkdir -p "$$workdir/$$install"; \
		unzip -qq "$(MRPACK)" "overrides/$(MAGICAVOXEL_LOADER_FILE)" -d "$$workdir/$$install"; \
		printf '%s  %s\n' "$(MAGICAVOXEL_LOADER_SHA256)" "$$workdir/$$install/overrides/$(MAGICAVOXEL_LOADER_FILE)" | sha256sum --check; \
	done; \
	echo "OK: one Loader override materializes with the pinned hash for client and server."

clean:
	rm -rf $(BUILD_DIR) bin
