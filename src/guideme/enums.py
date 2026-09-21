"""The two enum bases a caller declares their options and levels with.

A member's name is its wire key and its value is its rubric, the sentence the
model reads. Docstrings on members are not the rubric; the value is. Both bases
validate at class-definition time, so a rubric that could not be asked is an
error where it is written rather than on the first request.
"""

from enum import Enum
from typing import Self, final

from guideme.errors import ConfigError
from guideme.policy import MAX_LEVELS, MAX_OPTIONS, MIN_LEVELS

MIN_OPTIONS = 1
"""Fewest options a choice may carry."""


@final
class _Fallback(str):
    """A rubric marked as the member to fall back to when the policy says unsure.

    A `str` subclass, so the member's value is its rubric exactly like every
    other member's and the marking lives in the type rather than in a second
    attribute the caller could see.
    """

    __slots__ = ()


def fallback(rubric: str) -> str:
    """Mark the member to use when the policy says unsure. At most one per `Choice`.

    A `.otherwise(...)` on the question beats it; with neither, an unsure answer
    raises `UnsureError`.
    """
    return _Fallback(rubric)


def _require_text(cls: type[Enum], what: str) -> None:
    for member in cls:
        if not isinstance(member.value, str):
            detail = (
                f"{cls.__name__}.{member.name}: a {what}'s value must be its text, "
                f"got {type(member.value).__name__}"
            )
            raise ConfigError(detail)


class Choice(Enum):
    """Base for a choice over fixed options.

    A member's name is the key sent on the wire and read back; its value is the
    rubric. Mark at most one member with `fallback(...)`.
    """

    def __init_subclass__(cls) -> None:
        """Validate the rubric where it is written: option count, text, one fallback."""
        super().__init_subclass__()
        options = len(list(cls))
        if not MIN_OPTIONS <= options <= MAX_OPTIONS:
            detail = (
                f"{cls.__name__}: a Choice needs {MIN_OPTIONS}..={MAX_OPTIONS} options, "
                f"got {options}"
            )
            raise ConfigError(detail)
        _require_text(cls, "choice option")
        marked = [member.name for member in cls if isinstance(member.value, _Fallback)]
        if len(marked) > 1:
            detail = f"{cls.__name__}: only one member may be marked fallback, got {marked}"
            raise ConfigError(detail)

    @classmethod
    def rubric(cls) -> tuple[tuple[str, str], ...]:
        """`(key, rubric)` pairs in declaration order."""
        return tuple((member.name, member.value) for member in cls)

    @classmethod
    def fallback_member(cls) -> Self | None:
        """The `fallback(...)` member, if the rubric marks one."""
        for member in cls:
            if isinstance(member.value, _Fallback):
                return member
        return None

    @classmethod
    def from_key(cls, key: str) -> Self | None:
        """Map a wire key back to a member, or `None` when the key is not in the rubric."""
        return cls.__members__.get(key)


class Levels(Enum):
    """Base for a score over ordered levels.

    Declaration order is level order, low to high. A member's value is that
    level's description. Members compare against each other, so a score reads
    as `answer >= Frustration.frustrated`; comparing two different `Levels`
    classes is a type error.
    """

    def __init_subclass__(cls) -> None:
        """Validate the scale where it is written: level count and text."""
        super().__init_subclass__()
        levels = len(list(cls))
        if not MIN_LEVELS <= levels <= MAX_LEVELS:
            detail = (
                f"{cls.__name__}: a Levels needs {MIN_LEVELS}..={MAX_LEVELS} members, got {levels}"
            )
            raise ConfigError(detail)
        _require_text(cls, "level")

    @property
    def index(self) -> int:
        """This level's position, low to high."""
        return list(type(self)).index(self)

    @classmethod
    def levels(cls) -> tuple[str, ...]:
        """Level descriptions, low to high."""
        return tuple(member.value for member in cls)

    @classmethod
    def from_index(cls, index: int) -> Self | None:
        """Map a level index back to a member, or `None` when it is out of range."""
        members = list(cls)
        return members[index] if 0 <= index < len(members) else None

    def __lt__(self, other: Self) -> bool:
        """Order by declaration: an earlier level is lower."""
        return self.index < other.index

    def __le__(self, other: Self) -> bool:
        """Order by declaration: an earlier or equal level is not higher."""
        return self.index <= other.index

    def __gt__(self, other: Self) -> bool:
        """Order by declaration: a later level is higher."""
        return self.index > other.index

    def __ge__(self, other: Self) -> bool:
        """Order by declaration: a later or equal level is not lower."""
        return self.index >= other.index
