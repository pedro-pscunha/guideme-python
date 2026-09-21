"""Validated scalars shared by every layer.

A probability is parsed once, at the wire, and is a `Probability` from then on.
Nothing downstream re-validates, and nothing downstream can be handed a bare
`float` by mistake.
"""

from dataclasses import dataclass
from typing import NewType, Self, final, override

from guideme.errors import ConfigError, ProtocolError

Probability = NewType("Probability", float)
"""A probability in the closed unit interval; only `probability()` makes one."""

Confidence = NewType("Confidence", float)
"""A confidence in the closed unit interval; only `confidence()` makes one."""

Key = NewType("Key", str)
"""A runtime option key: the plain output of `choose_among`."""

Rank = NewType("Rank", int)
"""A runtime level index: the plain output of `score_levels`."""


def _unit(value: float, what: str) -> float:
    if not 0.0 <= value <= 1.0:
        detail = f"{what} {value} is outside 0..=1"
        raise ProtocolError(detail)
    return value


def probability(value: float) -> Probability:
    """Parse a probability. Raises `ProtocolError` outside `0..=1`."""
    return Probability(_unit(value, "probability"))


def confidence(value: float) -> Confidence:
    """Parse a confidence. Raises `ProtocolError` outside `0..=1`."""
    return Confidence(_unit(value, "confidence"))


@final
class ApiKey:
    """A TypeSafe API key.

    Prints as `ApiKey(***)` through both `repr` and `str`, is not JSON
    serialisable, and refuses to pickle. The value leaves this object only
    through `_expose`, which the HTTP client calls to build one header.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        """Wrap a key. Raises `ConfigError` when it is empty."""
        if not value:
            detail = "api key is empty"
            raise ConfigError(detail)
        self._value = value

    @override
    def __repr__(self) -> str:
        return "ApiKey(***)"

    @override
    def __str__(self) -> str:
        return "ApiKey(***)"

    @override
    def __eq__(self, other: object) -> bool:
        return isinstance(other, ApiKey) and other._value == self._value

    @override
    def __hash__(self) -> int:
        return hash(self._value)

    @override
    def __reduce__(self) -> str:
        message = "ApiKey cannot be pickled"
        raise TypeError(message)

    def _expose(self) -> str:
        """The secret. Read by `guideme.api.client` to build the header and nowhere else."""
        return self._value


@final
@dataclass(frozen=True, slots=True)
class Model:
    """A model name or alias accepted by the `model` field."""

    name: str

    @classmethod
    def latest(cls) -> Self:
        """`jev-latest`: the most recent stable release. The package default."""
        return cls("jev-latest")

    @classmethod
    def preview(cls) -> Self:
        """`jev-preview`: the most recent release, stable or not."""
        return cls("jev-preview")
