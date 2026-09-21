"""The one untyped edge: the caller's JSON-shaped state and instructions.

Everything else in the package is a declared type. `Json` is where the caller's
own data enters, and it is serialised immediately so a value that cannot be
represented fails at the boundary instead of somewhere inside the request.
"""

import json
from collections.abc import Mapping, Sequence

from guideme.errors import ConfigError

type Json = str | int | float | bool | Sequence[Json] | Mapping[str, Json] | None
"""Anything `json.dumps` accepts without a default hook. The caller's data, never ours.

`Sequence` and `Mapping` rather than `list` and `dict` because those two are invariant
in their parameters: a caller holding an ordinary `dict[str, str]` could not pass it
where a `dict[str, Json]` was wanted, which is the shape most state actually has. What
that admits and `json.dumps` refuses, a `bytes` or a mapping of its own, fails here as a
`ConfigError` before anything is sent.
"""


def dumps(value: Json, what: str) -> str:
    """Serialise compactly, or raise a typed error naming `what` was unserialisable."""
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        detail = f"{what} is not serialisable: {error}"
        raise ConfigError(detail) from error
