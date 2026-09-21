"""Questions as values: build one anywhere, ask it through a `Guide`.

A question carries what to send, the policy patch that applies to it alone, and
how to read the outcome back as the caller's own type. It is inert until asked,
so a house question can be a module constant.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Self, final

from guideme._json import Json
from guideme.enums import Choice, Levels
from guideme.errors import ConfigError, ProtocolError, UnsureError
from guideme.policy import (
    MAX_LEVELS,
    MAX_OPTIONS,
    MIN_LEVELS,
    ChoiceOutcome,
    NoulOutcome,
    Outcome,
    Policy,
    ScoreOutcome,
    Thresholds,
    Verdict,
)
from guideme.scalars import Confidence, Key, Probability, Rank


@final
@dataclass(frozen=True, slots=True)
class Ranked[C]:
    """A choice answer in full: the pick, its confidence, and `probabilities`.

    `probabilities` is the whole distribution, one entry per option in rubric order.
    """

    choice: C
    confidence: Confidence
    unsure: bool
    probabilities: tuple[tuple[C, Probability], ...]


@final
@dataclass(frozen=True, slots=True)
class Scored[L]:
    """A score answer in full: expected `value`, argmax `level`, confidence, `distribution`.

    `distribution` is the whole distribution, one entry per level in rubric order.
    """

    value: float
    level: L
    confidence: Confidence
    unsure: bool
    distribution: tuple[tuple[L, Probability], ...]


@final
@dataclass(frozen=True, slots=True)
class NoulCriteria:
    """What a yes and a no mean, when the question alone does not say."""

    yes: str
    no: str


@final
@dataclass(frozen=True, slots=True)
class NoulSpec:
    """A yes/no question as it goes on the wire."""

    criteria: NoulCriteria | None


@final
@dataclass(frozen=True, slots=True)
class ChoiceSpec:
    """A choice as it goes on the wire: `(key, rubric-or-None)` in declaration order."""

    rubric: tuple[tuple[str, str | None], ...]


@final
@dataclass(frozen=True, slots=True)
class ScoreSpec:
    """A score as it goes on the wire: level descriptions, low to high."""

    levels: tuple[str, ...]


type Spec = NoulSpec | ChoiceSpec | ScoreSpec
"""What a question puts on the wire, independent of the type it reads back as."""


def validate(spec: Spec) -> None:
    """Reject a rubric that could not be asked. Raises `ConfigError`."""
    match spec:
        case NoulSpec():
            pass  # a yes/no question has no rubric to check
        case ChoiceSpec(rubric=rubric):
            if not rubric:
                detail = "a choice needs at least one option"
                raise ConfigError(detail)
            if len(rubric) > MAX_OPTIONS:
                detail = f"a choice may have at most {MAX_OPTIONS} options, got {len(rubric)}"
                raise ConfigError(detail)
            if len({key for key, _ in rubric}) != len(rubric):
                detail = "duplicate option keys"
                raise ConfigError(detail)
        case ScoreSpec(levels=levels):
            if not MIN_LEVELS <= len(levels) <= MAX_LEVELS:
                detail = f"a score needs {MIN_LEVELS}..={MAX_LEVELS} levels, got {len(levels)}"
                raise ConfigError(detail)


type Reader[T] = Callable[[str, Outcome, Thresholds, T | None], T]
"""Reads one settled outcome as a `T`, using the given value when the policy says unsure."""


def _mismatch(qid: str, want: str, outcome: Outcome) -> ProtocolError:
    match outcome:
        case NoulOutcome():
            got = "noul"
        case ChoiceOutcome():
            got = "choice"
        case ScoreOutcome():
            got = "score"
    detail = f"question {qid} asked for {want}, answer is {got}"
    return ProtocolError(detail)


def _unsure[V](qid: str, value: float, threshold: float, chosen: V | None) -> V:
    if chosen is None:
        raise UnsureError(qid, value, threshold)
    return chosen


def _read_verdict(
    qid: str, outcome: Outcome, _thresholds: Thresholds, _chosen: Verdict | None
) -> Verdict:
    if not isinstance(outcome, NoulOutcome):
        raise _mismatch(qid, "noul", outcome)
    return outcome.verdict


def _read_bool(
    qid: str,
    outcome: Outcome,
    thresholds: Thresholds,
    chosen: bool | None,  # noqa: FBT001 -- the Reader shape's unsure value, not a flag
) -> bool:
    verdict = _read_verdict(qid, outcome, thresholds, None)
    match verdict.verdict:
        case "yes":
            return True
        case "no":
            return False
        case "unsure":
            to_yes = thresholds.yes_above - verdict.p
            to_no = verdict.p - thresholds.no_below
            nearer = thresholds.yes_above if to_yes <= to_no else thresholds.no_below
            return _unsure(qid, verdict.p, nearer, chosen)


def _choice_readers[C](
    rubric: tuple[tuple[str, str | None], ...],
    from_key: Callable[[str], C | None],
    marked_fallback: Callable[[], C | None],
) -> tuple[Reader[C], Reader[Ranked[C]]]:
    """Build the plain and detailed readers for one choice rubric."""
    keys = {key for key, _ in rubric}

    def option(qid: str, key: str) -> C:
        if key not in keys:
            detail = f"question {qid}: option {key!r} is not in the rubric"
            raise ProtocolError(detail)
        member = from_key(key)
        if member is None:
            detail = f"question {qid}: option {key!r} has no member"
            raise ProtocolError(detail)
        return member

    def detailed(
        qid: str, outcome: Outcome, _thresholds: Thresholds, _chosen: Ranked[C] | None
    ) -> Ranked[C]:
        if not isinstance(outcome, ChoiceOutcome):
            raise _mismatch(qid, "choice", outcome)
        if len(outcome.ranked) != len(rubric):
            detail = (
                f"question {qid}: answer has {len(outcome.ranked)} options, "
                f"rubric has {len(rubric)}"
            )
            raise ProtocolError(detail)
        return Ranked(
            choice=option(qid, outcome.key),
            confidence=outcome.confidence,
            unsure=outcome.unsure,
            probabilities=tuple((option(qid, key), p) for key, p in outcome.ranked),
        )

    def plain(qid: str, outcome: Outcome, thresholds: Thresholds, chosen: C | None) -> C:
        ranked = detailed(qid, outcome, thresholds, None)
        if not ranked.unsure:
            return ranked.choice
        ladder = chosen if chosen is not None else marked_fallback()
        return _unsure(qid, ranked.confidence, thresholds.min_confidence, ladder)

    return plain, detailed


def _score_readers[L](
    levels: tuple[str, ...], from_index: Callable[[int], L | None]
) -> tuple[Reader[L], Reader[Scored[L]]]:
    """Build the plain and detailed readers for one set of levels."""

    def level(qid: str, index: int) -> L:
        member = from_index(index)
        if member is None:
            detail = f"question {qid}: level {index} is not in the rubric"
            raise ProtocolError(detail)
        return member

    def detailed(
        qid: str, outcome: Outcome, _thresholds: Thresholds, _chosen: Scored[L] | None
    ) -> Scored[L]:
        if not isinstance(outcome, ScoreOutcome):
            raise _mismatch(qid, "score", outcome)
        if len(outcome.distribution) != len(levels):
            detail = (
                f"question {qid}: answer has {len(outcome.distribution)} levels, "
                f"question has {len(levels)}"
            )
            raise ProtocolError(detail)
        return Scored(
            value=outcome.value,
            level=level(qid, outcome.index),
            confidence=outcome.confidence,
            unsure=outcome.unsure,
            distribution=tuple(
                (level(qid, index), p) for index, p in enumerate(outcome.distribution)
            ),
        )

    def plain(qid: str, outcome: Outcome, thresholds: Thresholds, chosen: L | None) -> L:
        scored = detailed(qid, outcome, thresholds, None)
        if not scored.unsure:
            return scored.level
        return _unsure(qid, scored.confidence, thresholds.min_confidence, chosen)

    return plain, detailed


@dataclass(frozen=True, slots=True)
class Question[T]:
    """A question, its own policy patch, and how to read the outcome as a `T`.

    Inert until a `Guide` asks it. `T` is the plain output: `bool`, a `Choice`,
    a `Levels`, `Key`, `Rank`, or one of the detail types.
    """

    instructions: Json
    spec: Spec
    policy: Policy
    # How to read the outcome is decided by the constructor, and for a choice or a score
    # it is a fresh closure over the rubric, so two questions written the same way hold
    # two different functions. Out of `__eq__` and out of `repr`: what makes a question
    # the question it is is its instructions, its spec and its policy, and a function's
    # address in a `repr` is noise.
    _read: Reader[T] = field(compare=False, repr=False)
    _chosen: T | None

    def with_policy(self, policy: Policy) -> Self:
        """Merge a patch over this question's. Precedence: question, then guide, then defaults."""
        return replace(self, policy=policy.over(self.policy))

    def read(self, qid: str, outcome: Outcome, thresholds: Thresholds) -> T:
        """Read one settled outcome. Called by `ask.decode`, never by a caller."""
        return self._read(qid, outcome, thresholds, self._chosen)


class _Fallible[T](Question[T]):
    """A question whose plain reading can be unsure, so it accepts a value to fall back to."""

    __slots__ = ()

    def otherwise(self, value: T) -> Self:
        """Value to use when the policy says unsure. Beats a `fallback(...)` member."""
        return replace(self, _chosen=value)


class _Binary[T](Question[T]):
    """A question judged by a yes/no probability, so the noul boundaries apply."""

    __slots__ = ()

    def yes_above(self, p: float) -> Self:
        """`p >= yes_above` is yes."""
        return self.with_policy(Policy(yes_above=p))

    def no_below(self, p: float) -> Self:
        """`p <= no_below` is no."""
        return self.with_policy(Policy(no_below=p))


class _Confident[T](Question[T]):
    """A question judged by a reported confidence, so the confidence floor applies."""

    __slots__ = ()

    def min_confidence(self, c: float) -> Self:
        """`confidence < min_confidence` is unsure."""
        return self.with_policy(Policy(min_confidence=c))


@final
@dataclass(frozen=True, slots=True)
class DetailedNoul(_Binary[Verdict]):
    """A yes/no question read as a `Verdict`. Never fails on unsure."""


@final
@dataclass(frozen=True, slots=True)
class NoulQuestion(_Binary[bool], _Fallible[bool]):
    """A yes/no question read as a `bool`."""

    def criteria(self, yes: str, no: str) -> Self:
        """Describe what a yes and a no mean."""
        return replace(self, spec=NoulSpec(NoulCriteria(yes, no)))

    def detail(self) -> DetailedNoul:
        """Read the full `Verdict` instead. Any `.otherwise(...)` is dropped."""
        return DetailedNoul(
            instructions=self.instructions,
            spec=self.spec,
            policy=self.policy,
            _read=_read_verdict,
            _chosen=None,
        )


@final
@dataclass(frozen=True, slots=True)
class DetailedChoice[C](_Confident[Ranked[C]]):
    """A choice read as a `Ranked`. Never fails on unsure."""


@final
@dataclass(frozen=True, slots=True)
class ChoiceQuestion[C](_Confident[C], _Fallible[C]):
    """A choice read as one of the caller's options."""

    _detail: Reader[Ranked[C]] = field(compare=False, repr=False)

    def detail(self) -> DetailedChoice[C]:
        """Read the full `Ranked` instead. Any `.otherwise(...)` is dropped."""
        return DetailedChoice(
            instructions=self.instructions,
            spec=self.spec,
            policy=self.policy,
            _read=self._detail,
            _chosen=None,
        )


@final
@dataclass(frozen=True, slots=True)
class DetailedScore[L](_Confident[Scored[L]]):
    """A score read as a `Scored`. Never fails on unsure."""


@final
@dataclass(frozen=True, slots=True)
class ScoreQuestion[L](_Confident[L], _Fallible[L]):
    """A score read as one of the caller's levels."""

    _detail: Reader[Scored[L]] = field(compare=False, repr=False)

    def detail(self) -> DetailedScore[L]:
        """Read the full `Scored` instead. Any `.otherwise(...)` is dropped."""
        return DetailedScore(
            instructions=self.instructions,
            spec=self.spec,
            policy=self.policy,
            _read=self._detail,
            _chosen=None,
        )


def noul(instructions: Json) -> NoulQuestion:
    """A yes/no question. Plain output: `bool`."""
    spec = NoulSpec(None)
    validate(spec)
    return NoulQuestion(
        instructions=instructions, spec=spec, policy=Policy(), _read=_read_bool, _chosen=None
    )


def _choice[C](
    instructions: Json,
    rubric: tuple[tuple[str, str | None], ...],
    from_key: Callable[[str], C | None],
    marked_fallback: Callable[[], C | None],
) -> ChoiceQuestion[C]:
    spec = ChoiceSpec(rubric)
    validate(spec)
    plain, detailed = _choice_readers(rubric, from_key, marked_fallback)
    return ChoiceQuestion(
        instructions=instructions,
        spec=spec,
        policy=Policy(),
        _read=plain,
        _chosen=None,
        _detail=detailed,
    )


def choose[C: Choice](options: type[C], instructions: Json) -> ChoiceQuestion[C]:
    """Pick one of `options`' members. Plain output: that member."""
    return _choice(
        instructions,
        tuple((key, rubric) for key, rubric in options.rubric()),
        options.from_key,
        options.fallback_member,
    )


def choose_among(instructions: Json, options: Mapping[str, str | None]) -> ChoiceQuestion[Key]:
    """Pick one of the runtime options `(key, rubric)`. Plain output: `Key`.

    Takes 1 to 255 options, the same range a `Choice` enum takes. A rubric
    outside it, or one with a duplicate key, is a `ConfigError`.
    """
    return _choice(
        instructions,
        tuple(options.items()),
        Key,
        lambda: None,
    )


def _score[L](
    instructions: Json, levels: tuple[str, ...], from_index: Callable[[int], L | None]
) -> ScoreQuestion[L]:
    spec = ScoreSpec(levels)
    validate(spec)
    plain, detailed = _score_readers(levels, from_index)
    return ScoreQuestion(
        instructions=instructions,
        spec=spec,
        policy=Policy(),
        _read=plain,
        _chosen=None,
        _detail=detailed,
    )


def score[L: Levels](levels: type[L], instructions: Json) -> ScoreQuestion[L]:
    """Rate on `levels`' members, low to high. Plain output: the argmax member."""
    return _score(instructions, levels.levels(), levels.from_index)


def score_levels(instructions: Json, levels: Sequence[str]) -> ScoreQuestion[Rank]:
    """Rate on runtime levels, low to high. Plain output: `Rank`.

    Takes 2 to 10 levels, the same range a `Levels` enum takes; outside it is a
    `ConfigError`.

    A `str` is a `Sequence[str]` of its own characters, so `score_levels("…", "abc")`
    would quietly ask about a three-letter scale. It is a `ConfigError` instead.
    """
    if isinstance(levels, str | bytes):
        detail = (
            f"levels must be a sequence of level descriptions, got a single {type(levels).__name__}"
        )
        raise ConfigError(detail)
    return _score(instructions, tuple(levels), Rank)
