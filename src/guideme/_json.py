"""The one untyped edge: the caller's JSON-shaped state and instructions.

Everything else in the package is a declared type. `Json` is where the caller's
own data enters, and it is serialised immediately so a value that cannot be
represented fails at the boundary instead of somewhere inside the request.
"""

import json

from guideme.errors import ConfigError

type Json = str | int | float | bool | list[Json] | dict[str, Json] | None
"""Anything `json.dumps` accepts without a default hook. The caller's data, never ours."""


def dumps(value: Json, what: str) -> str:
    """Serialise compactly, or raise a typed error naming `what` was unserialisable."""
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        detail = f"{what} is not serialisable: {error}"
        raise ConfigError(detail) from error
