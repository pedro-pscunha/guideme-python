from typing import cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from guideme import ConfigError
from guideme.enums import Choice, Levels, fallback


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


def _one_level() -> type[Levels]:
    class OneLevel(Levels):
        only = "the only one"

    return OneLevel


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

    for build in (_two_fallbacks, _no_options, _option_that_is_not_text):
        with pytest.raises(ConfigError):
            _ = build()


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

    for build in (_one_level, _eleven_levels):
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
