"""Per-release opt-out of automatic hosting updates; publication remains unchanged."""
import argparse
from pathlib import Path
from release_decisions import trailer_value


def should_deploy(message: str) -> bool:
    value = trailer_value("Release-Only", message)
    if value is None:
        return True
    if value.lower() not in {"true", "false"}:
        raise ValueError("Release-Only must be true or false; refusing automatic deployment")
    return value.lower() == "false"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("message_file", type=Path)
    args = parser.parse_args()
    print("deploy=" + str(should_deploy(args.message_file.read_text(encoding="utf-8"))).lower())
