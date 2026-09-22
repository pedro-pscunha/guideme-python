"""The two enum bases a caller declares their options and levels with.

A member's name is its wire key and its value is its rubric, the sentence the
model reads. Docstrings on members are not the rubric; the value is. A rubric is
a bare string, or an `option(...)`, `level(...)` or `fallback(...)` that carries
examples beside the text and has them composed into it at the one point the
rubric becomes wire text. Both bases validate at class-definition time, so a
rubric that could not be asked is an error where it is written rather than on
the first request.
"""

from collections.abc import Iterable, Sequence
from enum import Enum
from typing import Self, final

from guideme.errors import ConfigError
from guideme.policy import MAX_LEVELS, MAX_OPTIONS, MIN_LEVELS

__all__ = ["Choice", "Levels", "fallback", "level", "option"]
"""What this module offers a caller, all of it re-exported from `guideme` itself.

`render`, `require_unshared_examples`, `require_no_counterexamples` and
`require_no_fallback` are internal despite their names: `guideme.question` imports them
and no caller ever does. They are in no tier, and leaving them out of this list is what
says so.
"""

MIN_OPTIONS = 1
"""Fewest options a choice may carry."""

EXAMPLES = "Examples: "
"""Opens the clause naming inputs that belong to this option or level."""

NOT_THIS = "Not this option: "
"""Opens the clause naming inputs that belong to some other option."""


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

    def __getnewargs_ex__(
        self,
    ) -> tuple[tuple[str, tuple[str, ...], tuple[str, ...]], dict[str, bool]]:
        """Hand `copy` and `pickle` every argument `__new__` needs, marking included.

        `str`'s own `__getnewargs__` supplies the text alone, which is one argument
        where this takes four, so without this a copied or pickled rubric raised a
        `TypeError`. A bare `str` rubric copied fine in 0.1.0 and has to keep doing so:
        `copy.deepcopy({"key": option(...)})` is exactly the shape a caller builds
        before handing it to `choose_among`.
        """
        return (str(self), self.examples, self.counterexamples), {"is_fallback": self.is_fallback}


def _items(what: str, items: Sequence[str] | None, named: str) -> tuple[str, ...]:
    """Check one clause's items: written means non-empty, each says something, no repeats.

    `None` is the clause left out. Anything else that is empty was written on purpose
    and says nothing, which is the caller's mistake rather than a default.

    A `str` is a `Sequence[str]` of its own characters, so `examples="refund"` would
    quietly become six one-letter examples. It is a `ConfigError` instead, the same
    way `score_levels` refuses a scale given as one string.
    """
    if items is None:
        return ()
    if isinstance(items, str | bytes):
        detail = (
            f"{what!r}: {named} must be a sequence of strings, got a single "
            f"{type(items).__name__}; wrap it in a list"
        )
        raise ConfigError(detail)
    if not items:
        detail = f"{what!r}: empty {named}=; a clause written on purpose must say something"
        raise ConfigError(detail)
    values = tuple(items)
    blank = [item for item in values if not item.strip()]
    if blank:
        detail = f"{what!r}: empty {named[:-1]} {blank[0]!r}; every entry must say something"
        raise ConfigError(detail)
    # Blank wins over this, which strip-first already gives: an item of one newline is
    # empty, not broken. The test is the literal codepoint, never `splitlines()`, which
    # here would also split on CR, VT, FF, FS, NEL, U+2028 and U+2029 and refuse seven
    # declarations the Rust SDK accepts.
    broken = [item for item in values if "\n" in item or "\r" in item]
    if broken:
        detail = (
            f"{what!r}: {named[:-1]} {broken[0]!r} may not contain a line break "
            f"(U+000A or U+000D); items are joined with '; ' onto one line, and a newline "
            f"in one would read as a clause the rubric never declared"
        )
        raise ConfigError(detail)
    seen: set[str] = set()
    for item in values:
        if item in seen:
            detail = f"{what!r}: duplicate {named[:-1]} {item!r}; each entry must be distinct"
            raise ConfigError(detail)
        seen.add(item)
    return values


def _rubric(
    rubric: str,
    examples: Sequence[str] | None,
    counterexamples: Sequence[str] | None,
    *,
    is_fallback: bool,
) -> str:
    """Check the parts and hold them on the rubric, unrendered.

    A blank rubric is refused only when examples are attached to it. Attaching them
    to nothing is the mistake; a blank rubric on its own was legal in 0.1.0, means
    exactly what a bare `""` member means, and a patch release does not get to make
    it an error.
    """
    shown = _items(rubric, examples, "examples")
    excluded = _items(rubric, counterexamples, "counterexamples")
    if (shown or excluded) and not rubric.strip():
        detail = (
            f"examples were attached to a blank rubric ({rubric!r}); they say what the "
            f"rubric covers, so there has to be something for them to say it about"
        )
        raise ConfigError(detail)
    ruled_out = set(excluded)
    both = [item for item in shown if item in ruled_out]
    if both:
        detail = (
            f"{rubric!r}: {both[0]!r} is both an example and a counterexample of it, which "
            f"says the input does and does not belong here; it may be an example of one "
            f"option and a counterexample of another, but not of the same one"
        )
        raise ConfigError(detail)
    return _Rubric(rubric, shown, excluded, is_fallback=is_fallback)


def option(
    rubric: str,
    *,
    examples: Sequence[str] | None = None,
    counterexamples: Sequence[str] | None = None,
) -> str:
    """A described alternative: what it covers, inputs that belong to it, inputs that do not.

    Use it for a `Choice` member, for a runtime option of `choose_among`, and for either
    side of a noul's `.criteria(...)`; a yes and a no are as confusable as two options.

    The value is still the rubric text; the examples are composed into it where the
    rubric goes on the wire, so an option written as a bare string and one written as
    `option("…")` send the same bytes, and both clauses keep the order they were
    written in. A string may be an example of one alternative and a counterexample of
    another: that is how two confusable ones are told apart.

    Leave a clause out to say there is none. An empty one written out says nothing and
    is refused: an `examples=[]`, a clause given as one string rather than a sequence of
    them, a blank entry, a repeat within one clause, a string given as both an example
    and a counterexample of this one alternative, and examples attached to a blank
    rubric are each a `ConfigError` where the option is written. A blank rubric with no
    examples is not: that is what it has always meant.
    """
    return _rubric(rubric, examples, counterexamples, is_fallback=False)


def level(rubric: str, *, examples: Sequence[str] | None = None) -> str:
    """A score level: what it means, and inputs that score here.

    An example listed under a level is the statement that such an input scores that
    level; its place in the ordered scale is what says which score. There are no
    counterexamples: "not this option" means nothing on an ordered scale, so the
    level below or above is what an input that does not belong here scores.
    """
    return _rubric(rubric, examples, None, is_fallback=False)


def fallback(
    rubric: str,
    *,
    examples: Sequence[str] | None = None,
    counterexamples: Sequence[str] | None = None,
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


def require_unshared_examples(where: str, rubrics: Iterable[tuple[str, str | None]]) -> None:
    """Refuse one string offered as an example of two alternatives of the same question.

    It would say the input belongs to both, which cannot be true. The reverse is legal
    and is the point of the feature: the same string as an example of one alternative
    and a counterexample of another is how two confusable ones are told apart.
    """
    seen: dict[str, str] = {}
    for name, rubric in rubrics:
        if not isinstance(rubric, _Rubric):
            continue
        for example in rubric.examples:
            first = seen.setdefault(example, name)
            if first != name:
                detail = (
                    f"{where}: {example!r} is an example of both {first} and {name}, so it "
                    f"says one input belongs to two alternatives; make it a counterexample "
                    f"of one of them instead"
                )
                raise ConfigError(detail)


def require_no_fallback(where: str, rubric: str | None) -> None:
    """Refuse a `fallback(...)` where there is no `Choice` member for it to mark.

    The runtime constructors answer in a `Key`, a `Rank` or a `bool`, none of which has
    a member to fall back to, so the marking has nothing to act on. Dropping it would
    leave a caller believing an unsure answer is handled when it raises instead.
    """
    if isinstance(rubric, _Rubric) and rubric.is_fallback:
        detail = (
            f"{where}: fallback(...) marks a member of a Choice and there is none here; "
            f"use .otherwise(value) on the question"
        )
        raise ConfigError(detail)


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
        require_unshared_examples(cls.__name__, ((m.name, m.value) for m in cls))

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
        require_unshared_examples(cls.__name__, ((m.name, m.value) for m in cls))

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
