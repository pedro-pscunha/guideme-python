from collections.abc import Callable
from typing import cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from guideme import ConfigError
from guideme.enums import Choice, Levels, fallback, level, option


def _compare(low: Levels, high: Levels) -> bool:
    """Compare two levels through a signature too wide to see whether they share a scale.

    That is what a caller holding a `Levels`-typed value has; the checker's half of the
    proof is `tests/typing/expect_errors/levels_cross_compare.py`.
    """
    return low >= high


def _two_fallbacks() -> type[Choice]:
    class TwoFallbacks(Choice):
        a = fallback("first")
        b = fallback("second")

    return TwoFallbacks


def _no_options() -> type[Choice]:
    class Empty(Choice):
        pass

    return Empty


def _option_that_is_not_text() -> type[Choice]:
    class NotText(Choice):
        a = 1

    return NotText


def _duplicate_choice_rubric() -> type[Choice]:
    class Duplicated(Choice):
        a = "the same sentence"
        b = "the same sentence"

    return Duplicated


def _duplicate_level_rubric() -> type[Levels]:
    class Duplicated(Levels):
        low = "the same sentence"
        high = "the same sentence"

    return Duplicated


def _fallback_on_a_level() -> type[Levels]:
    class Marked(Levels):
        can_wait = fallback("Can wait")
        today = "Today"

    return Marked


def _one_level() -> type[Levels]:
    class OneLevel(Levels):
        only = "the only one"

    return OneLevel


def _a_blank_rubric() -> type[Choice]:
    class Blank(Choice):
        a = option("   ")

    return Blank


def _an_examples_clause_written_empty() -> type[Choice]:
    class Empty(Choice):
        a = option("Payments", examples=[])

    return Empty


def _an_empty_tuple_written_out() -> type[Choice]:
    # `None` is how a clause is left out, so an empty sequence is always a clause
    # written on purpose that says nothing -- whatever type the caller reached for.
    class Empty(Choice):
        a = option("Payments", counterexamples=())

    return Empty


def _a_blank_example() -> type[Choice]:
    class Blank(Choice):
        a = option("Payments", examples=["My card was charged twice", " "])

    return Blank


def _a_repeated_example() -> type[Choice]:
    class Repeated(Choice):
        a = option("Payments", examples=["Where is my refund?", "Where is my refund?"])

    return Repeated


def _one_example_of_two_options() -> type[Choice]:
    class Shared(Choice):
        billing = option("Payments", examples=["Where is my refund?"])
        technical = option("Bugs", examples=["Where is my refund?"])

    return Shared


def _one_example_of_two_levels() -> type[Levels]:
    class Shared(Levels):
        cosmetic = level("No impact", examples=["a typo in a label"])
        blocking = level("No workaround exists", examples=["a typo in a label"])

    return Shared


def _an_example_that_is_also_a_counterexample() -> type[Choice]:
    class Both(Choice):
        billing = option(
            "Payments",
            examples=["Where is my refund?"],
            counterexamples=["Where is my refund?"],
        )

    return Both


def _a_counterexample_on_a_level() -> type[Levels]:
    class Marked(Levels):
        cosmetic = option("No impact", counterexamples=["cannot log in"])
        blocking = level("No workaround exists")

    return Marked


def _eleven_levels() -> type[Levels]:
    class ElevenLevels(Levels):
        l0 = "a"
        l1 = "b"
        l2 = "c"
        l3 = "d"
        l4 = "e"
        l5 = "f"
        l6 = "g"
        l7 = "h"
        l8 = "i"
        l9 = "j"
        l10 = "k"

    return ElevenLevels


def test_choice_exposes_rubric_keys_and_fallback_and_refuses_a_bad_definition() -> None:
    class Department(Choice):
        billing = "Payments, invoicing, refunds"
        technical = "Bugs, outages, integrations"
        sales = fallback("Pricing, upgrades, new accounts")

    assert Department.rubric() == (
        ("billing", "Payments, invoicing, refunds"),
        ("technical", "Bugs, outages, integrations"),
        ("sales", "Pricing, upgrades, new accounts"),
    )
    assert Department.fallback_member() is Department.sales
    assert Department.sales.value == "Pricing, upgrades, new accounts"
    assert Department.from_key("technical") is Department.technical
    assert Department.from_key("marketing") is None


def test_levels_are_totally_ordered_by_declaration() -> None:
    class Frustration(Levels):
        calm = "Calm and polite"
        frustrated = "Frustrated"
        very_angry = "Very angry"

    assert list(Frustration) == sorted(Frustration)
    assert Frustration.calm < Frustration.frustrated < Frustration.very_angry
    assert Frustration.very_angry >= Frustration.frustrated > Frustration.calm
    assert Frustration.levels() == ("Calm and polite", "Frustrated", "Very angry")
    assert Frustration.from_index(2) is Frustration.very_angry
    assert Frustration.from_index(3) is None

    class Urgency(Levels):
        can_wait = "Can wait"
        today = "Today"

    assert _compare(Frustration.calm, Frustration.very_angry) is False
    with pytest.raises(TypeError):
        _ = _compare(Frustration.calm, Urgency.today)


REFUSED: list[Callable[[], type[Choice] | type[Levels]]] = [
    _two_fallbacks,
    _no_options,
    _option_that_is_not_text,
    _one_level,
    _eleven_levels,
    _duplicate_choice_rubric,
    _duplicate_level_rubric,
    _fallback_on_a_level,
    _a_blank_rubric,
    _an_examples_clause_written_empty,
    _an_empty_tuple_written_out,
    _a_blank_example,
    _a_repeated_example,
    _a_counterexample_on_a_level,
    _one_example_of_two_options,
    _one_example_of_two_levels,
    _an_example_that_is_also_a_counterexample,
]

REFUSED_IDS = [
    "two_fallbacks",
    "no_options",
    "an_option_that_is_not_text",
    "one_level",
    "eleven_levels",
    "two_options_with_the_same_rubric",
    "two_levels_with_the_same_rubric",
    "a_fallback_marker_on_a_levels",
    "a_blank_rubric",
    "an_examples_clause_written_empty",
    "an_empty_tuple_written_out",
    "a_blank_example",
    "a_repeated_example",
    "a_counterexample_on_a_level",
    "one_example_of_two_options",
    "one_example_of_two_levels",
    "an_example_that_is_also_a_counterexample",
]


@pytest.mark.parametrize("build", REFUSED, ids=REFUSED_IDS)
def test_a_rubric_outside_the_rules_is_refused_at_class_definition(
    build: Callable[[], type[Choice] | type[Levels]],
) -> None:
    with pytest.raises(ConfigError):
        _ = build()


@given(st.lists(st.text(min_size=1), min_size=2, max_size=10, unique=True))
def test_level_index_round_trips_and_ordering_agrees_with_it(descriptions: list[str]) -> None:
    # The functional API goes through the same metaclass as a class statement,
    # so the result is a Levels subclass that __init_subclass__ has validated.
    generated = cast(
        "type[Levels]",
        Levels("Generated", {f"l{i}": text for i, text in enumerate(descriptions)}),
    )
    members = list(generated)
    assert [member.index for member in members] == list(range(len(descriptions)))
    assert generated.levels() == tuple(descriptions)
    assert all(generated.from_index(i) is member for i, member in enumerate(members))
    assert all(
        (a <= b) == (a.index <= b.index) and (a > b) == (a.index > b.index)
        for a in members
        for b in members
    )
