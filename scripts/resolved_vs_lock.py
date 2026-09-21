"""Say whether `latest-deps` re-resolved, by printing what it got beside what the lock pins.

uv prints "Resolved N packages" whether it re-resolved or reused the lock, and today every
runtime dependency is pinned at a floor that is also its newest release, so a correct run and
a job silently reading `uv.lock` produce identical output. This is what tells them apart.

The committed lock is read out of git, because `uv sync --upgrade` has already rewritten the
copy on disk by the time this runs. `actions/checkout` at `fetch-depth: 1` still has HEAD, so
this needs no deeper fetch.

Run from the repository root, inside the environment the upgrade resolved.
"""

import subprocess
import tomllib
from importlib import metadata
from typing import cast

RUNTIME = ("httpx", "pydantic", "opentelemetry-api")
"""The runtime dependencies, named one by one: a move in any of them is the thing to see."""

LOCK = "HEAD:uv.lock"
"""The lock as committed. `--upgrade` rewrote the working copy, so git holds the only original."""


def committed_lock() -> str:
    """The text of `uv.lock` at HEAD."""
    # A fixed argument list and no shell; nothing here comes from outside this file.
    finished = subprocess.run(  # noqa: S603 -- a fixed argument list, no shell, no input
        ["git", "show", LOCK],  # noqa: S607 -- `git` off PATH, as every other CI step calls it
        capture_output=True,
        text=True,
        check=True,
    )
    return finished.stdout


def pinned(lock_text: str) -> dict[str, str]:
    """Every package the committed lock pins, by name. A lock it cannot read is a failure."""
    # `tomllib.loads` is typed `dict[str, Any]`. `dict[str, object]` is the same type with
    # the `Any` taken out, which is always sound, and every value read out of it below is
    # narrowed by an isinstance before it is used.
    parsed = cast("dict[str, object]", tomllib.loads(lock_text))
    packages = parsed.get("package")
    if not isinstance(packages, list):
        message = f"{LOCK} has no [[package]] array"
        raise SystemExit(message)
    found: dict[str, str] = {}
    # Same again: the isinstance proved a list, and its elements are whatever TOML holds.
    for entry in cast("list[object]", packages):
        if not isinstance(entry, dict):
            message = f"{LOCK} has a [[package]] entry that is not a table"
            raise SystemExit(message)
        record = cast("dict[str, object]", entry)
        name = record.get("name")
        pin = record.get("version")
        if not isinstance(name, str) or not isinstance(pin, str):
            message = f"{LOCK} has a [[package]] entry with no string name and version"
            raise SystemExit(message)
        found[name] = pin
    return found


def installed(name: str) -> str | None:
    """The version in this environment, or `None` where the lock carries it for another platform."""
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def main() -> None:
    """Print the runtime dependencies one by one, then a count of everything else that moved."""
    locked = pinned(committed_lock())

    for name in RUNTIME:
        got = installed(name) or "(not installed)"
        was = locked.get(name, "(not in the lock)")
        note = "same as the lock" if got == was else f"NEWER than the lock's {was}"
        print(f"{name:<20} resolved {got:<14} {note}")

    moved: list[str] = []
    # Packages the lock carries for another platform raise here rather than resolving, so
    # they are counted and never looked up: a number nobody can mistake for drift.
    elsewhere = 0
    for name, was in locked.items():
        if name == "guideme":
            continue
        got_or_none = installed(name)
        if got_or_none is None:
            elsewhere += 1
        elif got_or_none != was:
            moved.append(name)

    print()
    if moved:
        print(f"{len(moved)} resolved newer than the lock: {', '.join(sorted(moved))}")
    else:
        print("nothing in the lock has a newer release today; the range and the lock agree")
    print(f"({elsewhere} locked for another platform, not installed here)")


if __name__ == "__main__":
    main()
