from typing import cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from guideme import ConfigError, Policy, ProtocolError, Thresholds
from guideme.ask import Plan, Reply, decode, encode
from guideme.policy import NoulOutcome, Verdict
from guideme.question import noul
from guideme.scalars import probability

LEAF = "leaf"


def _shapes() -> st.SearchStrategy[object]:
    keys = st.text(alphabet="abc", min_size=1, max_size=2)
    return st.recursive(
        st.builds(noul, st.text(min_size=1, max_size=5)),
        lambda children: st.one_of(
            st.lists(children, min_size=1, max_size=3).map(tuple),
            st.lists(children, min_size=1, max_size=3),
            st.dictionaries(keys, children, min_size=1, max_size=3),
        ),
        max_leaves=8,
    )


def _outline(value: object) -> object:
    """The container structure with every leaf, question or answer, replaced alike."""
    if isinstance(value, tuple):
        return tuple(_outline(item) for item in cast("tuple[object, ...]", value))
    if isinstance(value, list):
        return [_outline(item) for item in cast("list[object]", value)]
    if isinstance(value, dict):
        return {key: _outline(item) for key, item in cast("dict[object, object]", value).items()}
    return LEAF


def _all_yes(plan: Plan) -> Reply:
    yes = NoulOutcome(Verdict("yes", probability(1.0)))
    return {qid: (yes, thresholds) for qid, thresholds in plan.thresholds.items()}


@given(shape=_shapes())
def test_ids_follow_encounter_order_and_decode_restores_the_shape(shape: object) -> None:
    plan = Plan(base=Policy())
    claim = encode(shape, plan)
    ids = list(plan.specs)
    assert ids == [f"q{index}" for index in range(len(ids))]
    assert list(plan.thresholds) == ids

    answers = decode(claim, _all_yes(plan))
    assert _outline(answers) == _outline(shape)
    assert _outline(claim) == _outline(shape)


def test_an_empty_shape_plans_nothing_and_anything_else_is_refused() -> None:
    empty = Plan(base=Policy())
    assert encode((), empty) == ()
    assert not empty.specs

    with pytest.raises(ConfigError):
        _ = encode("a question shape is not a string", empty)

    repeated = noul("asked twice")
    plan = Plan(base=Policy(yes_above=0.9, no_below=0.1))
    claim = encode({"first": repeated, "second": repeated.yes_above(0.6)}, plan)
    assert list(plan.specs) == ["q0", "q1"]
    assert plan.thresholds["q0"] == Thresholds(yes_above=0.9, no_below=0.1)
    assert plan.thresholds["q1"] == Thresholds(yes_above=0.6, no_below=0.1)

    with pytest.raises(ProtocolError):
        _ = decode(claim, {})
