"""Defer the push-triggered tuning sync when the same commit also ships a pack release.

server-tuning.yml's push trigger is deliberately scoped to tuning-only paths
(server-config.json, scripts/sync-server-config.py, tests/test_sync_server_config.py,
.github/workflows/server-tuning.yml) precisely so an edit to those files alone can
sync immediately -- see that workflow's own header comment. But a single commit can
ALSO touch a pack-release path (mods/**, pack.toml, index.toml, config/**,
defaultconfigs/**, resourcepacks/**, shaderpacks/** -- release.yml's own path filter)
at the same time, e.g. a Dynamic Atmosphere version bump landing together with a
tuning-key rename for that same DA version. In that case the push-triggered sync
would race the Release -> Server update deploy chain and could write into a config
file for a DA version that isn't live yet. When that happens, defer: the existing
workflow_run("Server update") trigger on this same workflow already re-runs the sync
after that deploy actually completes, which is the correct timing.
"""
import fnmatch

RELEASE_TRIGGERING_PATTERNS = (
    "pack.toml",
    "index.toml",
    "mods/*",
    "config/*",
    "defaultconfigs/*",
    "resourcepacks/*",
    "shaderpacks/*",
)


def touches_release_path(changed_paths):
    return any(
        fnmatch.fnmatch(path, pattern)
        for path in changed_paths
        for pattern in RELEASE_TRIGGERING_PATTERNS
    )


def should_sync_immediately(changed_paths):
    """changed_paths: every file touched by the push-triggering commit."""
    return not touches_release_path(changed_paths)


if __name__ == "__main__":
    import sys
    changed = [line.strip() for line in sys.stdin if line.strip()]
    print("sync=" + str(should_sync_immediately(changed)).lower())
