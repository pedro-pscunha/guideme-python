import asyncio
import os

import pytest

from guideme import (
    AsyncGuide,
    Choice,
    Guide,
    Key,
    Levels,
    Scored,
    choose,
    choose_among,
    fallback,
    noul,
    option,
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


AMBIGUOUS = "About those shoes - what is the situation with the money side of things?"
"""A ticket two options both half fit, where the examples are what tells them apart."""

POLICY = "The shop's rules"
STATUS = "One customer's open case"

POLICY_EXAMPLES = ["How many days do I have to send it back?", "Can I return a sale item?"]
STATUS_EXAMPLES = ["Where is my refund?", "I posted the shoes back last week and heard nothing"]

BARE = {"return_policy": POLICY, "return_status": STATUS}
"""The rubrics as bare strings. Deliberately vague: the ticket is near a coin flip."""

DESCRIBED = {
    "return_policy": option(POLICY, examples=POLICY_EXAMPLES, counterexamples=[STATUS_EXAMPLES[1]]),
    "return_status": option(STATUS, examples=STATUS_EXAMPLES, counterexamples=[POLICY_EXAMPLES[1]]),
}
"""The same two rubrics, with the examples that tell the two apart."""


WORKAROUND = (
    "Our nightly export job has been failing since Tuesday. We pull the numbers by hand for now."
)
"""A ticket a vague `Urgent` / `Not urgent` calls urgent, and the examples call otherwise."""

URGENT = "Urgent"
NOT_URGENT = "Not urgent"

URGENT_EXAMPLES = ["customers cannot log in", "money is moving to the wrong place"]
NOT_URGENT_EXAMPLES = ["a broken job with a manual workaround", "a cosmetic bug"]


def _return_status(guide: Guide, options: dict[str, str]) -> float:
    ranked = guide.ask(
        choose_among("What is the customer asking about?", options).detail(), AMBIGUOUS
    )
    return dict(ranked.probabilities)[Key("return_status")]


def _urgent(guide: Guide, yes: str, no: str) -> float:
    verdict = guide.ask(noul("Is this ticket urgent?").criteria(yes, no).detail(), WORKAROUND)
    return verdict.p


def test_examples_move_the_distribution_towards_the_alternative_they_describe() -> None:
    """The invariant the feature exists for, not a number the model is not stable to.

    Both halves hold the rubric text constant across their two asks, so the examples
    are the only thing that changed. Measured on 2026-09-21 against `jev-1.13.0`: a
    choice over two vague options goes 0.50 bare to 0.88 described, and a noul over
    vague criteria goes 0.75 plain to 0.17 described, three and four runs each. The
    noul half is the one where the plain rubric is outright wrong: one of the
    not-urgent examples is what this ticket describes.
    """
    guide = Guide.from_env()
    try:
        bare = _return_status(guide, BARE)
        described = _return_status(guide, DESCRIBED)
        plain = _urgent(guide, URGENT, NOT_URGENT)
        told = _urgent(
            guide,
            option(URGENT, examples=URGENT_EXAMPLES, counterexamples=NOT_URGENT_EXAMPLES),
            option(NOT_URGENT, examples=NOT_URGENT_EXAMPLES, counterexamples=URGENT_EXAMPLES),
        )
    finally:
        guide.close()
    assert described > bare
    assert told < plain
