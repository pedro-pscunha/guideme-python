import asyncio
import os

import pytest

from guideme import (
    AsyncGuide,
    Choice,
    Guide,
    Levels,
    Scored,
    choose,
    fallback,
    noul,
    score,
)
from guideme.question import Question
from guideme.telemetry import ASK_SPAN

from .conftest import Recorded, attributes

TICKET = "Help! My payouts have been failing for 3 days and nobody answers. I was charged twice."

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("TYPESAFE_API_KEY"), reason="no TYPESAFE_API_KEY"),
]


class Department(Choice):
    billing = "Payments, invoicing, refunds"
    technical = "Bugs, outages, integrations"
    sales = fallback("Pricing, upgrades, new accounts")


class Frustration(Levels):
    calm = "Calm and polite"
    frustrated = "Frustrated"
    very_angry = "Very angry"


def documented_batch() -> tuple[
    Question[bool],
    Question[Department],
    Question[Scored[Frustration]],
    dict[str, Question[bool]],
]:
    """The batch from the README, built once so both kinds of guide ask the same thing.

    Declared as `Question[...]` rather than the concrete kinds because `dict` is
    invariant: a `dict[str, NoulQuestion]` is not a `dict[str, Question[bool]]`, and the
    overload asks for the latter. Written inline, as the README writes it, the literal
    infers the right type on its own.
    """
    # pylint: disable=no-member  # the fluent chain returns Self off a mixin; pyright follows it
    return (
        noul("Is this urgent?").yes_above(0.7).no_below(0.3).otherwise(False),
        choose(Department, "Which team should handle this?").min_confidence(0.6),
        score(Frustration, "How frustrated is the customer?").detail(),
        {"spam": noul("Is it spam?"), "vip": noul("Is the sender a VIP?")},
    )


def check(
    urgent: bool,  # noqa: FBT001 -- what the first question of the batch reads back as
    dept: Department,
    mood: Scored[Frustration],
    flags: dict[str, bool],
) -> None:
    """Every result is the caller's own type, and the choice is exhaustively matchable."""
    assert isinstance(urgent, bool)
    match dept:
        case Department.billing | Department.technical | Department.sales:
            pass
    assert isinstance(mood, Scored)
    assert isinstance(mood.level, Frustration)
    assert mood.level >= Frustration.calm
    assert 0.0 <= mood.value <= 2.0
    assert len(mood.distribution) == 3
    assert set(flags) == {"spam", "vip"}
    assert all(isinstance(flag, bool) for flag in flags.values())


def billed(spans: Recorded) -> int:
    """The input tokens the ask span recorded."""
    tokens = attributes(spans.one(ASK_SPAN))["gen_ai.usage.input_tokens"]
    assert isinstance(tokens, int)
    return tokens


def test_the_documented_batch_is_typed_end_to_end_through_guide(spans: Recorded) -> None:
    guide = Guide.from_env()
    try:
        urgent, dept, mood, flags = guide.ask(documented_batch(), TICKET)
        models = guide.models()
    finally:
        guide.close()
    check(urgent, dept, mood, flags)
    assert billed(spans) > 0
    assert any(model.name == "jev-latest" for model in models)


def test_the_documented_batch_is_typed_end_to_end_through_async_guide(spans: Recorded) -> None:
    async def run() -> tuple[bool, Department, Scored[Frustration], dict[str, bool]]:
        guide = AsyncGuide.from_env()
        try:
            return await guide.ask(documented_batch(), TICKET)
        finally:
            await guide.close()

    urgent, dept, mood, flags = asyncio.run(run())
    check(urgent, dept, mood, flags)
    assert billed(spans) > 0
