from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


def _copy_with_hard_abort(
    *,
    source_path: Path,
    target_path: Path,
    chunk_bytes: int,
    abort_after_bytes: int,
    sleep_seconds: float,
) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    copied = 0
    with source_path.open("rb") as source_handle:
        with target_path.open("wb") as target_handle:
            while True:
                chunk = source_handle.read(max(1, int(chunk_bytes)))
                if not chunk:
                    break
                target_handle.write(chunk)
                target_handle.flush()
                os.fsync(target_handle.fileno())
                copied += len(chunk)
                if int(abort_after_bytes) > 0 and copied >= int(abort_after_bytes):
                    os._exit(97)
                if float(sleep_seconds) > 0:
                    time.sleep(float(sleep_seconds))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Internal helper for snapshot/restore crash-consistency drill. "
            "Copies bytes from source to target and hard-aborts mid-copy."
        )
    )
    parser.add_argument("--source-path", required=True)
    parser.add_argument("--target-path", required=True)
    parser.add_argument("--chunk-bytes", type=int, default=4096)
    parser.add_argument("--abort-after-bytes", type=int, default=65536)
    parser.add_argument("--sleep-seconds", type=float, default=0.01)
    args = parser.parse_args(argv)

    source_path = Path(str(args.source_path)).expanduser()
    target_path = Path(str(args.target_path)).expanduser()
    if not source_path.exists():
        print(
            f"[snapshot-restore-crash-target] ERROR: source file does not exist: {source_path}",
            file=sys.stderr,
        )
        return 2
    if int(args.chunk_bytes) <= 0:
        print("[snapshot-restore-crash-target] ERROR: --chunk-bytes must be > 0.", file=sys.stderr)
        return 2
    if int(args.abort_after_bytes) <= 0:
        print("[snapshot-restore-crash-target] ERROR: --abort-after-bytes must be > 0.", file=sys.stderr)
        return 2
    if float(args.sleep_seconds) < 0:
        print("[snapshot-restore-crash-target] ERROR: --sleep-seconds must be >= 0.", file=sys.stderr)
        return 2

    try:
        _copy_with_hard_abort(
            source_path=source_path,
            target_path=target_path,
            chunk_bytes=int(args.chunk_bytes),
            abort_after_bytes=int(args.abort_after_bytes),
            sleep_seconds=float(args.sleep_seconds),
        )
    except Exception as exc:
        print(f"[snapshot-restore-crash-target] ERROR: {exc}", file=sys.stderr)
        return 1

    print(
        "[snapshot-restore-crash-target] ERROR: copy completed without hard abort; "
        "abort threshold is too high for this source size.",
        file=sys.stderr,
    )
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
