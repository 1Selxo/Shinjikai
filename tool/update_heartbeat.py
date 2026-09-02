import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


def update_heartbeat(path: Path, minimum_age: timedelta, now: datetime) -> bool:
    previous = None
    if path.exists():
        with path.open("r", encoding="utf-8") as source:
            value = json.load(source).get("last_successful_automation_check")
        if value:
            previous = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if previous is not None and now - previous < minimum_age:
        return False

    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as output:
        json.dump(
            {"last_successful_automation_check": now.astimezone(timezone.utc).isoformat()},
            output,
            indent=2,
            sort_keys=True,
        )
        output.write("\n")
    os.replace(temporary, path)
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--minimum-days", type=int, default=30)
    args = parser.parse_args()
    changed = update_heartbeat(
        args.path,
        timedelta(days=args.minimum_days),
        datetime.now(timezone.utc),
    )
    print(f"heartbeat_changed={'true' if changed else 'false'}")


if __name__ == "__main__":
    main()
