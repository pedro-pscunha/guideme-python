"""The two enum bases a caller declares their options and levels with.

A member's name is its wire key and its value is its rubric, the sentence the
model reads. Docstrings on members are not the rubric; the value is. A rubric is
a bare string, or an `option(...)`, `level(...)` or `fallback(...)` that carries
examples beside the text and has them composed into it at the one point the
rubric becomes wire text. Both bases validate at class-definition time, so a
rubric that could not be asked is an error where it is written rather than on
the first request.
"""

from collections.abc import Sequence
from enum import Enum
from typing import Self, final

from guideme.errors import ConfigError
from guideme.policy import MAX_LEVELS, MAX_OPTIONS, MIN_LEVELS

MIN_OPTIONS = 1
"""Fewest options a choice may carry."""

EXAMPLES = "Examples: "
"""Opens the clause naming inputs that belong to this option or level."""

NOT_THIS = "Not this option: "
"""Opens the clause naming inputs that belong to some other option."""

_UNSET: tuple[str, ...] = ()
"""The `examples` and `counterexamples` default, and how an empty sequence written on
purpose is told from an argument left out: CPython hands out one empty tuple, so the
`examples=[]` this refuses is never this object."""


@final
class _Rubric(str):  # noqa: SLOT000 -- a str subclass may not carry non-empty __slots__
    """A rubric held as its parts: the text, its examples, its counterexamples.

    A `str` subclass whose `str` value is the bare text, so a member's value reads
    exactly like a bare string's and nothing downstream meets a new type. The parts
    ride along as attributes until `render` composes them. `str` is a variable-length
    built-in, so its subclasses cannot have slots and the parts live in the instance
    dictionary; they are set in `__new__` because a `str` is immutable and carries its
    text from there.
    """

    examples: tuple[str, ...] = ()
    counterexamples: tuple[str, ...] = ()
    is_fallback: bool = False

    def __new__(
        cls,
        rubric: str,
        examples: tuple[str, ...],
        counterexamples: tuple[str, ...],
        *,
        is_fallback: bool,
    ) -> Self:
        """Carry the parts on the string without changing what the string says."""
        self = super().__new__(cls, rubric)
        self.examples = examples
        self.counterexamples = counterexamples
        self.is_fallback = is_fallback
        return self


def _items(what: str, items: Sequence[str], named: str) -> tuple[str, ...]:
    """Check one clause's items: written means non-empty, each says something, no repeats."""
    if items is _UNSET:
        return ()
    if not items:
        detail = f"{what!r}: {named}= is empty; a clause written on purpose must say something"
        raise ConfigError(detail)
    values = tuple(items)
    blank = [item for item in values if not item.strip()]
    if blank:
        detail = f"{what!r}: every entry in {named} must say something, got {blank[0]!r}"
        raise ConfigError(detail)
    if len(set(values)) != len(values):
        detail = f"{what!r}: {named} repeats an entry; each must be distinct"
        raise ConfigError(detail)
    return values


def _rubric(
    rubric: str,
    examples: Sequence[str],
    counterexamples: Sequence[str],
    *,
    is_fallback: bool,
) -> str:
    """Check the parts and hold them on the rubric, unrendered."""
    if not rubric.strip():
        detail = f"a rubric must say something, got {rubric!r}"
        raise ConfigError(detail)
    return _Rubric(
        rubric,
        _items(rubric, examples, "examples"),
        _items(rubric, counterexamples, "counterexamples"),
        is_fallback=is_fallback,
    )


def option(
    rubric: str,
    *,
    examples: Sequence[str] = _UNSET,
    counterexamples: Sequence[str] = _UNSET,
) -> str:
    """A choice option: what it covers, inputs that belong to it, inputs that do not.

    The value is still the rubric text; the examples are composed into it where the
    rubric goes on the wire, so an option written as a bare string and one written as
    `option("…")` send the same bytes. A string may be an example of one option and a
    counterexample of another: that is how two confusable options are told apart.

    An empty rubric, an empty entry, an `examples=[]` written out, or a repeat within
    one clause is a `ConfigError` where the option is written.
    """
    return _rubric(rubric, examples, counterexamples, is_fallback=False)


def level(rubric: str, *, examples: Sequence[str] = _UNSET) -> str:
    """A score level: what it means, and inputs that score here.

    An example listed under a level is the statement that such an input scores that
    level; its place in the ordered scale is what says which score. There are no
    counterexamples: "not this option" means nothing on an ordered scale, so the
    level below or above is what an input that does not belong here scores.
    """
    return _rubric(rubric, examples, _UNSET, is_fallback=False)


def fallback(
    rubric: str,
    *,
    examples: Sequence[str] = _UNSET,
    counterexamples: Sequence[str] = _UNSET,
) -> str:
    """Mark the member to use when the policy says unsure. At most one per `Choice`.

    An `option(...)` in every other respect, examples included. A `.otherwise(...)` on
    the question beats it; with neither, an unsure answer raises `UnsureError`.
    """
    return _rubric(rubric, examples, counterexamples, is_fallback=True)


def render(rubric: str) -> str:
    """Compose a rubric's parts into the text the model reads.

    Clauses are joined with a newline and the items of one with `"; "`, and the text
    is used verbatim: never trimmed, never re-punctuated, so an example ending in `?`
    does not become `Where is my refund?.`. A bare string, and a rubric carrying no
    parts, come back byte for byte, which is what leaves an existing caller's request
    unchanged. The rendered string is a cross-SDK contract item; `guideme-rust`
    composes the same one in its derive macro.
    """
    if not isinstance(rubric, _Rubric):
        return rubric
    if not rubric.examples and not rubric.counterexamples:
        return str(rubric)
    lines = [str(rubric)]
    if rubric.examples:
        lines.append(EXAMPLES + "; ".join(rubric.examples))
    if rubric.counterexamples:
        lines.append(NOT_THIS + "; ".join(rubric.counterexamples))
    return "\n".join(lines)


def require_no_counterexamples(where: str, rubric: str) -> None:
    """Refuse counterexamples on a level, rather than rendering or dropping them.

    `level(...)` does not offer them, so this is an `option(...)` written where a level
    belongs. Silently losing what it carries is the failure this raises instead.
    """
    if isinstance(rubric, _Rubric) and rubric.counterexamples:
        detail = (
            f"{where}: counterexamples are not allowed on a level, which is a position on "
            f"an ordered scale; use level(...), which takes examples only"
        )
        raise ConfigError(detail)


def _require_distinct(cls: type[Enum]) -> None:
    """Refuse a repeated rubric: Python turns the second member into an alias of the first."""
    aliased = [name for name, member in cls.__members__.items() if member.name != name]
    if aliased:
        detail = (
            f"{cls.__name__}: {aliased} repeat another member's text, so Python makes each "
            f"an alias and the rubric loses it; every member's text must be unique"
        )
        raise ConfigError(detail)


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

    A `Choice` needs 1 to 255 options. The count is checked on the class
    statement, so a rubric outside that range is a `ConfigError` where the enum
    is written rather than on the first ask.
    """

    def __init_subclass__(cls) -> None:
        """Validate where it is written: distinct text, option count, text, one fallback."""
        super().__init_subclass__()
        _require_distinct(cls)
        options = len(list(cls))
        if not MIN_OPTIONS <= options <= MAX_OPTIONS:
            detail = (
                f"{cls.__name__}: a Choice needs {MIN_OPTIONS}..={MAX_OPTIONS} options, "
                f"got {options}"
            )
            raise ConfigError(detail)
        _require_text(cls, "choice option")
        marked = [
            member.name
            for member in cls
            if isinstance(member.value, _Rubric) and member.value.is_fallback
        ]
        if len(marked) > 1:
            detail = f"{cls.__name__}: only one member may be marked fallback, got {marked}"
            raise ConfigError(detail)

    @classmethod
    def rubric(cls) -> tuple[tuple[str, str], ...]:
        """`(key, rendered rubric)` pairs in declaration order.

        This is where an `option(...)`'s examples are composed into its text, so a
        member cannot reach the wire having quietly lost them.
        """
        return tuple((member.name, render(member.value)) for member in cls)

    @classmethod
    def fallback_member(cls) -> Self | None:
        """The `fallback(...)` member, if the rubric marks one."""
        for member in cls:
            if isinstance(member.value, _Rubric) and member.value.is_fallback:
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
    classes is a type error, and a `TypeError` at runtime for a caller who is not
    type-checked.

    A `Levels` needs 2 to 10 members. The count is checked on the class
    statement, so a rubric outside that range is a `ConfigError` where the enum
    is written rather than on the first ask.
    """

    def __init_subclass__(cls) -> None:
        """Validate where it is written: distinct text, level count, text, no fallback."""
        super().__init_subclass__()
        _require_distinct(cls)
        levels = len(list(cls))
        if not MIN_LEVELS <= levels <= MAX_LEVELS:
            detail = (
                f"{cls.__name__}: a Levels needs {MIN_LEVELS}..={MAX_LEVELS} members, got {levels}"
            )
            raise ConfigError(detail)
        _require_text(cls, "level")
        for member in cls:
            if isinstance(member.value, _Rubric) and member.value.is_fallback:
                detail = (
                    f"{cls.__name__}.{member.name}: fallback(...) is not allowed on Levels; "
                    f"use .otherwise(level) on the question"
                )
                raise ConfigError(detail)
            require_no_counterexamples(f"{cls.__name__}.{member.name}", member.value)

    @property
    def index(self) -> int:
        """This level's position, low to high."""
        return list(type(self)).index(self)

    @classmethod
    def levels(cls) -> tuple[str, ...]:
        """Rendered level descriptions, low to high.

        This is where a `level(...)`'s examples are composed into its text.
        """
        return tuple(render(member.value) for member in cls)

    @classmethod
    def from_index(cls, index: int) -> Self | None:
        """Map a level index back to a member, or `None` when it is out of range."""
        members = list(cls)
        return members[index] if 0 <= index < len(members) else None

    def __lt__(self, other: Self) -> bool:
        """Order by declaration: an earlier level is lower. Another scale raises."""
        if type(other) is not type(self):
            return NotImplemented
        return self.index < other.index

    def __le__(self, other: Self) -> bool:
        """Order by declaration: an earlier or equal level is not higher. Another scale raises."""
        if type(other) is not type(self):
            return NotImplemented
        return self.index <= other.index

    def __gt__(self, other: Self) -> bool:
        """Order by declaration: a later level is higher. Another scale raises."""
        if type(other) is not type(self):
            return NotImplemented
        return self.index > other.index

    def __ge__(self, other: Self) -> bool:
        """Order by declaration: a later or equal level is not lower. Another scale raises."""
        if type(other) is not type(self):
            return NotImplemented
        return self.index >= other.index
