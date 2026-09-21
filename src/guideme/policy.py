"""The pure decision layer: thresholds in, labelled outcome out.

No I/O, no generics, no caller enums. Its inputs and outputs are keys and level
indices, which is why `spec/vectors/policy.json` can pin `resolve` for every
guideme SDK at once. A change here is a change to that contract.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, final

from guideme.errors import ConfigError, ProtocolError
from guideme.scalars import Confidence, Probability

MAX_OPTIONS = 255
"""Most options a choice may carry."""

MIN_LEVELS = 2
"""Fewest levels a score may carry; one level is not a scale."""

MAX_LEVELS = 10
"""Most levels a score may carry."""


@final
@dataclass(frozen=True, slots=True)
class Thresholds:
    """Settled, validated thresholds: the input of `resolve` and of every golden vector."""

    yes_above: float = 0.5
    no_below: float = 0.5
    min_confidence: float = 0.0

    def __post_init__(self) -> None:
        """Validate: every field in `0..=1`, and `no_below` no greater than `yes_above`."""
        for name, value in (
            ("yes_above", self.yes_above),
            ("no_below", self.no_below),
            ("min_confidence", self.min_confidence),
        ):
            if not 0.0 <= value <= 1.0:
                detail = f"{name} = {value} is outside 0..=1"
                raise ConfigError(detail)
        if self.no_below > self.yes_above:
            detail = f"no_below {self.no_below} > yes_above {self.yes_above}"
            raise ConfigError(detail)


@final
@dataclass(frozen=True, slots=True)
class Policy:
    """A policy patch. Unset fields defer to the next layer: the guide, then the defaults."""

    yes_above: float | None = None
    no_below: float | None = None
    min_confidence: float | None = None

    def over(self, base: "Policy") -> "Policy":
        """Merge: `self` wins over `base`, field by field."""
        return Policy(
            yes_above=base.yes_above if self.yes_above is None else self.yes_above,
            no_below=base.no_below if self.no_below is None else self.no_below,
            min_confidence=(
                base.min_confidence if self.min_confidence is None else self.min_confidence
            ),
        )

    def settle(self) -> Thresholds:
        """Fill unset fields with the defaults and validate. Raises `ConfigError` if invalid."""
        defaults = Thresholds()
        return Thresholds(
            yes_above=defaults.yes_above if self.yes_above is None else self.yes_above,
            no_below=defaults.no_below if self.no_below is None else self.no_below,
            min_confidence=(
                defaults.min_confidence if self.min_confidence is None else self.min_confidence
            ),
        )


type VerdictLabel = Literal["yes", "no", "unsure"]
"""The three readings of a noul answer. A `match` over it is checked for exhaustiveness."""


@final
@dataclass(frozen=True, slots=True)
class Verdict:
    """The three-way reading of a noul answer, with the probability it was read from."""

    verdict: VerdictLabel
    p: Probability


@final
@dataclass(frozen=True, slots=True)
class NoulAnswer:
    """A parsed yes/no answer."""

    noul: Probability


@final
@dataclass(frozen=True, slots=True)
class ChoiceAnswer:
    """A parsed choice answer; keys are wire option keys."""

    choice: str
    probabilities: Mapping[str, Probability]
    confidence: Confidence


@final
@dataclass(frozen=True, slots=True)
class ScoreAnswer:
    """A parsed score answer; keys are level indices."""

    score: float
    legend: Mapping[int, str]
    probabilities: Mapping[int, Probability]
    confidence: Confidence


type Answer = NoulAnswer | ChoiceAnswer | ScoreAnswer
"""What the wire layer hands `resolve`, already validated into scalars."""


@final
@dataclass(frozen=True, slots=True)
class NoulOutcome:
    """What the policy concludes about a yes/no answer."""

    verdict: Verdict


@final
@dataclass(frozen=True, slots=True)
class ChoiceOutcome:
    """What the policy concludes about a choice.

    `ranked` is by descending probability, ties broken by ascending key.
    """

    key: str
    confidence: Confidence
    unsure: bool
    ranked: tuple[tuple[str, Probability], ...]


@final
@dataclass(frozen=True, slots=True)
class ScoreOutcome:
    """What the policy concludes about a score.

    Levels are positional: element `i` of `distribution` and of `legend` is level `i`.
    """

    index: int
    value: float
    confidence: Confidence
    unsure: bool
    distribution: tuple[Probability, ...]
    legend: tuple[str, ...]


type Outcome = NoulOutcome | ChoiceOutcome | ScoreOutcome
"""What `resolve` returns: untyped in the caller's terms, keys and indices only."""


def _noul(answer: NoulAnswer, thresholds: Thresholds) -> NoulOutcome:
    p = answer.noul
    if p >= thresholds.yes_above:
        return NoulOutcome(Verdict("yes", p))
    if p <= thresholds.no_below:
        return NoulOutcome(Verdict("no", p))
    return NoulOutcome(Verdict("unsure", p))


def _choice(answer: ChoiceAnswer, thresholds: Thresholds) -> ChoiceOutcome:
    if answer.choice not in answer.probabilities:
        detail = f"choice {answer.choice!r} is not in the distribution"
        raise ProtocolError(detail)
    ranked = tuple(sorted(answer.probabilities.items(), key=lambda kv: (-kv[1], kv[0])))
    return ChoiceOutcome(
        key=answer.choice,
        confidence=answer.confidence,
        unsure=answer.confidence < thresholds.min_confidence,
        ranked=ranked,
    )


def _score(answer: ScoreAnswer, thresholds: Thresholds) -> ScoreOutcome:
    levels = len(answer.legend)
    contiguous = sorted(answer.legend) == list(range(levels)) and sorted(
        answer.probabilities
    ) == list(range(levels))
    if not MIN_LEVELS <= levels <= MAX_LEVELS or not contiguous:
        detail = (
            "score legend/probabilities must be contiguous levels 0..n with "
            f"{MIN_LEVELS} <= n <= {MAX_LEVELS}, got {levels}"
        )
        raise ProtocolError(detail)
    top = levels - 1
    if not 0.0 <= answer.score <= top:
        detail = f"score {answer.score} is outside 0..={top}"
        raise ProtocolError(detail)
    index = max(range(levels), key=lambda i: (answer.probabilities[i], -i))
    return ScoreOutcome(
        index=index,
        value=answer.score,
        confidence=answer.confidence,
        unsure=answer.confidence < thresholds.min_confidence,
        distribution=tuple(answer.probabilities[i] for i in range(levels)),
        legend=tuple(answer.legend[i] for i in range(levels)),
    )


def resolve(answer: Answer, thresholds: Thresholds) -> Outcome:
    """Apply thresholds to one answer.

    Pure and total except for a malformed answer: a chosen key absent from the
    distribution, a legend and distribution that are not the same contiguous
    `0..n`, or a score outside the level range, each raise `ProtocolError`.
    """
    match answer:
        case NoulAnswer():
            return _noul(answer, thresholds)
        case ChoiceAnswer():
            return _choice(answer, thresholds)
        case ScoreAnswer():
            return _score(answer, thresholds)
