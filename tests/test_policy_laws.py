from itertools import pairwise

import pytest
from hypothesis import given
from hypothesis import strategies as st

from guideme import ConfigError, Policy, ProtocolError, Thresholds
from guideme.policy import (
    ChoiceAnswer,
    ChoiceOutcome,
    NoulAnswer,
    NoulOutcome,
    ScoreAnswer,
    ScoreOutcome,
    resolve,
)
from guideme.scalars import confidence, probability

UNIT = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)
OPTION_KEYS = st.text(alphabet="abcdefgh", min_size=1, max_size=3)
ABSENT_KEY = "_"
RANK = {"no": 0, "unsure": 1, "yes": 2}


@st.composite
def thresholds(draw: st.DrawFn) -> Thresholds:
    low, high = sorted((draw(UNIT), draw(UNIT)))
    return Thresholds(yes_above=high, no_below=low, min_confidence=draw(UNIT))


def _patch() -> st.SearchStrategy[Policy]:
    optional = st.none() | UNIT
    return st.builds(Policy, yes_above=optional, no_below=optional, min_confidence=optional)


@given(p=UNIT, t=thresholds())
def test_noul_is_unsure_exactly_inside_the_open_band(p: float, t: Thresholds) -> None:
    outcome = resolve(NoulAnswer(probability(p)), t)
    assert isinstance(outcome, NoulOutcome)
    assert outcome.verdict.p == p
    assert (outcome.verdict.verdict == "unsure") == (t.no_below < p < t.yes_above)
    assert (outcome.verdict.verdict == "yes") == (p >= t.yes_above)
    assert (outcome.verdict.verdict == "no") == (p < t.yes_above and p <= t.no_below)


@given(first=UNIT, second=UNIT, t=thresholds())
def test_noul_verdict_is_monotone_in_p(first: float, second: float, t: Thresholds) -> None:
    low, high = sorted((first, second))
    lower = resolve(NoulAnswer(probability(low)), t)
    higher = resolve(NoulAnswer(probability(high)), t)
    assert isinstance(lower, NoulOutcome)
    assert isinstance(higher, NoulOutcome)
    assert RANK[lower.verdict.verdict] <= RANK[higher.verdict.verdict]


@given(
    raw=st.dictionaries(OPTION_KEYS, UNIT, min_size=1, max_size=6),
    c=UNIT,
    t=thresholds(),
)
def test_choice_ranks_by_descending_probability_and_is_unsure_below_the_floor(
    raw: dict[str, float], c: float, t: Thresholds
) -> None:
    probabilities = {key: probability(value) for key, value in raw.items()}
    chosen = next(iter(probabilities))
    outcome = resolve(ChoiceAnswer(chosen, probabilities, confidence(c)), t)
    assert isinstance(outcome, ChoiceOutcome)
    assert outcome.key == chosen
    assert outcome.unsure == (c < t.min_confidence)
    assert dict(outcome.ranked) == probabilities
    pairs = list(outcome.ranked)
    assert all(
        earlier[1] > later[1] or (earlier[1] == later[1] and earlier[0] < later[0])
        for earlier, later in pairwise(pairs)
    )
    with pytest.raises(ProtocolError):
        _ = resolve(ChoiceAnswer(ABSENT_KEY, probabilities, confidence(c)), t)


@given(
    weights=st.lists(UNIT, min_size=2, max_size=10),
    position=UNIT,
    c=UNIT,
    t=thresholds(),
)
def test_score_index_is_the_argmax_with_ties_going_to_the_lowest_level(
    weights: list[float], position: float, c: float, t: Thresholds
) -> None:
    levels = len(weights)
    probabilities = {i: probability(w) for i, w in enumerate(weights)}
    legend = {i: f"level {i}" for i in range(levels)}
    value = position * (levels - 1)
    outcome = resolve(ScoreAnswer(value, legend, probabilities, confidence(c)), t)
    assert isinstance(outcome, ScoreOutcome)
    assert outcome.index == weights.index(max(weights))
    assert outcome.value == value
    assert outcome.unsure == (c < t.min_confidence)
    assert outcome.distribution == tuple(probabilities[i] for i in range(levels))
    assert outcome.legend == tuple(legend[i] for i in range(levels))
    with pytest.raises(ProtocolError):
        _ = resolve(ScoreAnswer(float(levels), legend, probabilities, confidence(c)), t)


@given(question=_patch(), guide=_patch())
def test_a_question_patch_wins_over_the_guide_and_settle_fills_the_defaults(
    question: Policy, guide: Policy
) -> None:
    merged = question.over(guide)
    assert merged.yes_above == (
        guide.yes_above if question.yes_above is None else question.yes_above
    )
    assert merged.no_below == (guide.no_below if question.no_below is None else question.no_below)
    assert merged.min_confidence == (
        guide.min_confidence if question.min_confidence is None else question.min_confidence
    )
    defaults = Thresholds()
    yes = defaults.yes_above if merged.yes_above is None else merged.yes_above
    no = defaults.no_below if merged.no_below is None else merged.no_below
    floor = defaults.min_confidence if merged.min_confidence is None else merged.min_confidence
    if no > yes:
        with pytest.raises(ConfigError):
            _ = merged.settle()
        return
    assert merged.settle() == Thresholds(yes_above=yes, no_below=no, min_confidence=floor)
