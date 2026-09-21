"""The documented example, type-checked.

This is the code the README shows, with an `assert_type` after every `ask`. It is never
executed; `pyright` checks it as part of the gate, so the documented example cannot drift
from what the package actually infers.
"""

from typing import assert_type

from guideme import (
    ApiKey,
    AsyncGuide,
    Choice,
    Guide,
    Levels,
    Policy,
    Probability,
    Ranked,
    Scored,
    Verdict,
    choose,
    fallback,
    noul,
    score,
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


def escalate() -> None:
    """Stand-in for the caller's own work."""


def route_billing() -> None:
    """Stand-in for the caller's own work."""


def route_tech() -> None:
    """Stand-in for the caller's own work."""


def route_sales() -> None:
    """Stand-in for the caller's own work."""


def prioritise() -> None:
    """Stand-in for the caller's own work."""


def billing() -> None:
    """Stand-in for the caller's own work."""


def other() -> None:
    """Stand-in for the caller's own work."""


def review(p: Probability) -> None:
    """Stand-in for the caller's own work."""


CAUTIOUS = Policy(yes_above=0.7, no_below=0.3)


def triage(ticket: Json) -> None:
    """A yes/no is an `if`, a choice is an exhaustive `match`, a score is a comparison."""
    guide = Guide.from_env()

    if guide.ask(noul("Should this ticket be escalated?"), ticket):
        escalate()

    match guide.ask(
        choose(Department, "Which team should handle this?").min_confidence(0.6), ticket
    ):
        case Department.billing:
            route_billing()
        case Department.technical:
            route_tech()
        case Department.sales:
            route_sales()

    if guide.ask(score(Frustration, "How frustrated is the customer?"), ticket) >= (
        Frustration.frustrated
    ):
        prioritise()

    urgent, dept, mood, flags = guide.ask(
        (
            noul("Is this urgent?").yes_above(0.7).no_below(0.3).otherwise(False),
            choose(Department, "Which team?").min_confidence(0.6),
            score(Frustration, "How frustrated?").detail(),
            {"spam": noul("Is it spam?"), "vip": noul("Is the sender a VIP?")},
        ),
        ticket,
    )
    # pyright infers tuple[bool, Department, Scored[Frustration], dict[str, bool]]
    _ = assert_type(urgent, bool)
    _ = assert_type(dept, Department)
    _ = assert_type(mood, Scored[Frustration])
    _ = assert_type(flags, dict[str, bool])

    if urgent or mood.value > 1.5 or flags["vip"]:
        prioritise()

    guide.close()


def house_policy(key: ApiKey, ticket: Json) -> None:
    """A house policy is a module constant; a scoped copy patches over it."""
    guide = Guide.builder().api_key(key).policy(CAUTIOUS).build()
    strict = guide.with_policy(Policy(min_confidence=0.8))

    reading = guide.ask(noul("Is this about billing?").detail(), ticket)
    _ = assert_type(reading, Verdict)
    match reading.verdict:
        case "yes":
            billing()
        case "no":
            other()
        case "unsure":
            review(reading.p)

    picked = strict.ask(choose(Department, "Which team?").detail(), ticket)
    _ = assert_type(picked, Ranked[Department])

    guide.close()


async def triage_async(ticket: Json) -> None:
    """The same surface, one await later."""
    guide = AsyncGuide.from_env()
    verdict: Verdict = await guide.ask(noul("Is this about billing?").detail(), ticket)
    _ = assert_type(verdict, Verdict)
    await guide.close()
