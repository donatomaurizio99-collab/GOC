from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def _write_json_atomic(path: Path, payload: dict[str, Any], *, temp_path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Internal helper for multi-db atomic-switch drill. Writes a staged pointer update "
            "and optionally aborts before atomic replace."
        )
    )
    parser.add_argument("--pointer-path", required=True)
    parser.add_argument("--target", required=True, choices=("primary", "candidate"))
    parser.add_argument("--mode", required=True, choices=("abort-before-replace", "commit"))
    args = parser.parse_args(argv)

    pointer_path = Path(str(args.pointer_path)).expanduser()
    if not pointer_path.exists():
        print(f"[multi-db-atomic-switch-target] ERROR: pointer file missing: {pointer_path}", file=sys.stderr)
        return 2

    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[multi-db-atomic-switch-target] ERROR: invalid pointer json: {exc}", file=sys.stderr)
        return 2
    if not isinstance(pointer, dict):
        print("[multi-db-atomic-switch-target] ERROR: pointer json must be an object.", file=sys.stderr)
        return 2

    pointer["active"] = str(args.target)
    pointer["updated_by"] = "multi-db-atomic-switch-target"
    temp_path = pointer_path.with_name(pointer_path.name + ".switch.tmp")
    try:
        _write_json_atomic(pointer_path, pointer, temp_path=temp_path)
    except Exception as exc:
        print(f"[multi-db-atomic-switch-target] ERROR: failed to write staged pointer: {exc}", file=sys.stderr)
        return 1

    if str(args.mode) == "abort-before-replace":
        os._exit(97)

    try:
        os.replace(str(temp_path), str(pointer_path))
    except Exception as exc:
        print(f"[multi-db-atomic-switch-target] ERROR: atomic replace failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({"ok": True, "mode": str(args.mode), "target": str(args.target)}, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
