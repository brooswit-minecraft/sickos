#!/usr/bin/env python3
"""One save-on attempt, using the pinned Schematic RCON helper. No retries."""
import hashlib
import importlib.util
import os
from pathlib import Path
import sys

HELPER = Path(__file__).resolve().parents[1] / ".schematic-tools/scripts/server_deploy.py"
# server_deploy.py at f27f43679e34a4975e8e1b18cdf6528877c6e66d.
HELPER_SHA256 = "73db30c0915e1b83732646f351e9c9b46994aff2b156b6600bc2f711871cf41d"
RESULTS = {
    "Automatic saving is now enabled": "enabled",
    "Saving is already turned on": "already-enabled",
}


def load_helper():
    if hashlib.sha256(HELPER.read_bytes()).hexdigest() != HELPER_SHA256:
        raise ValueError("Helper integrity mismatch")
    spec = importlib.util.spec_from_file_location("save_on_rcon", HELPER)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper


def main():
    try:
        # No command, target, or arbitrary CLI input is supported.
        if len(sys.argv) != 1:
            raise ValueError("Unexpected arguments")
        helper = load_helper()
        host, port, password, _ = helper.rcon_config(os.environ)
        response = helper.rcon_command(host, port, password, "save-on")
        result = RESULTS[response]  # Exact allowlist; never print raw peer data.
    except Exception:
        print("save-on: unconfirmed; no retry attempted", file=sys.stderr)
        return 1
    print("save-on: " + result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
