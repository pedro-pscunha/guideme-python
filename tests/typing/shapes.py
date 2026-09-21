"""Pyright proofs: what `ask` returns for every shape it carries types through.

Never executed. `pyright` checks it as part of the gate, and `assert_type` fails the
gate when an inferred type is not exactly the one written here. Everything lives in
functions so that importing the module could not open a connection.
"""

from typing import assert_type

from guideme import (
    AsyncGuide,
    Choice,
    Guide,
    Key,
    Levels,
    Rank,
    Ranked,
    Scored,
    Verdict,
    choose,
    choose_among,
    fallback,
    noul,
    score,
    score_levels,
)
from guideme._json import Json


class Department(Choice):
    billing = "Payments, invoicing, refunds"
    technical = "Bugs, outages, integrations"
    sales = fallback("Pricing, upgrades, new accounts")


class Frustration(Levels):
    calm = "Calm and polite"
    frustrated = "Frustrated"
    very_angry = "Very angry"


def single(guide: Guide, state: Json) -> None:
    """One question reads back as its own plain type."""
    _ = assert_type(guide.ask(noul("Urgent?"), state), bool)
    _ = assert_type(guide.ask(choose(Department, "Which team?"), state), Department)
    _ = assert_type(guide.ask(score(Frustration, "How cross?"), state), Frustration)
    _ = assert_type(guide.ask(choose_among("Who owns it?", {"ana": None}), state), Key)
    _ = assert_type(guide.ask(score_levels("How urgent?", ["soon", "now"]), state), Rank)


def detailed(guide: Guide, state: Json) -> None:
    """`.detail()` swaps the plain reading for the full one, and never fails on unsure."""
    _ = assert_type(guide.ask(noul("Urgent?").detail(), state), Verdict)
    _ = assert_type(guide.ask(choose(Department, "Which?").detail(), state), Ranked[Department])
    _ = assert_type(guide.ask(score(Frustration, "How?").detail(), state), Scored[Frustration])


def containers(guide: Guide, state: Json) -> None:
    """A list and a map of questions read back as a list and a map of their answers."""
    _ = assert_type(guide.ask([noul("Urgent?"), noul("Spam?")], state), list[bool])
    _ = assert_type(guide.ask({"spam": noul("Spam?")}, state), dict[str, bool])
    _ = assert_type(guide.ask({1: choose(Department, "Which?")}, state), dict[int, Department])


def tuples(guide: Guide, state: Json) -> None:
    """Every tuple arity the overloads cover, carrying each position's type separately."""
    one = noul("Urgent?")
    _ = assert_type(guide.ask((one,), state), tuple[bool])
    _ = assert_type(guide.ask((one, one), state), tuple[bool, bool])
    _ = assert_type(guide.ask((one, one, one), state), tuple[bool, bool, bool])
    _ = assert_type(guide.ask((one, one, one, one), state), tuple[bool, bool, bool, bool])
    _ = assert_type(
        guide.ask((one, one, one, one, one), state), tuple[bool, bool, bool, bool, bool]
    )
    _ = assert_type(
        guide.ask((one, one, one, one, one, one), state),
        tuple[bool, bool, bool, bool, bool, bool],
    )
    _ = assert_type(
        guide.ask((one, one, one, one, one, one, one), state),
        tuple[bool, bool, bool, bool, bool, bool, bool],
    )
    _ = assert_type(
        guide.ask((one, one, one, one, one, one, one, one), state),
        tuple[bool, bool, bool, bool, bool, bool, bool, bool],
    )
    _ = assert_type(
        guide.ask(
            (noul("Urgent?"), choose(Department, "Which?"), score(Frustration, "How?")), state
        ),
        tuple[bool, Department, Frustration],
    )


def documented(guide: Guide, state: Json) -> None:
    """The batch the README shows: a tuple whose last element is a map of questions."""
    _ = assert_type(
        guide.ask(
            (
                noul("Is this urgent?").yes_above(0.7).no_below(0.3).otherwise(False),
                choose(Department, "Which team?").min_confidence(0.6),
                score(Frustration, "How frustrated?").detail(),
                {"spam": noul("Is it spam?"), "vip": noul("Is the sender a VIP?")},
            ),
            state,
        ),
        tuple[bool, Department, Scored[Frustration], dict[str, bool]],
    )
    _ = assert_type(
        guide.ask((noul("Urgent?"), [noul("Spam?"), noul("Bot?")]), state),
        tuple[bool, list[bool]],
    )


async def awaited(guide: AsyncGuide, state: Json) -> None:
    """The asyncio surface answers with the same types, one await later."""
    _ = assert_type(await guide.ask(noul("Urgent?"), state), bool)
    _ = assert_type(await guide.ask(noul("Urgent?").detail(), state), Verdict)
    _ = assert_type(await guide.ask(choose(Department, "Which?"), state), Department)
    _ = assert_type(
        await guide.ask((noul("Urgent?"), score(Frustration, "How?").detail()), state),
        tuple[bool, Scored[Frustration]],
    )
    _ = assert_type(await guide.ask({"spam": noul("Spam?")}, state), dict[str, bool])
