import pytest
from hypothesis import given
from hypothesis import strategies as st
from pytest_httpserver import HTTPServer

from guideme import Choice, Levels, choose, fallback, level, option, score
from guideme.enums import render

from .conftest import (
    JSON,
    TICKET,
    Json,
    Runner,
    as_object,
    expect_post,
    narrow,
    reply,
    validator,
)

check_request = validator("request")

BILLING = "Payments, invoicing, refunds"
TECHNICAL = "Bugs, outages, integrations"
COSMETIC = "No impact to functionality"

# The golden table of the cross-SDK design: the inputs, and the exact bytes both SDKs
# render them to. `guideme-rust` reproduces this table from its derive macro, and
# spec/vectors/rubric.json is where the two are held to each other.
GOLDEN: list[tuple[str, str]] = [
    (option(BILLING), BILLING),
    (
        option(TECHNICAL, examples=["502 on every request"]),
        f"{TECHNICAL}\nExamples: 502 on every request",
    ),
    (
        option(BILLING, examples=["My card was charged twice", "Where is my refund?"]),
        f"{BILLING}\nExamples: My card was charged twice; Where is my refund?",
    ),
    (
        option(
            BILLING,
            examples=["My card was charged twice"],
            counterexamples=["The dashboard is down"],
        ),
        f"{BILLING}\nExamples: My card was charged twice\nNot this option: The dashboard is down",
    ),
    (
        option(BILLING, counterexamples=["The dashboard is down"]),
        f"{BILLING}\nNot this option: The dashboard is down",
    ),
    (
        level(COSMETIC, examples=["typo in a label", "misaligned icon"]),
        f"{COSMETIC}\nExamples: typo in a label; misaligned icon",
    ),
]

GOLDEN_IDS = [
    "no_parts",
    "one_example",
    "two_examples",
    "examples_and_a_counterexample",
    "a_counterexample_only",
    "a_level_with_examples",
]


@pytest.mark.parametrize(("rubric", "expected"), GOLDEN, ids=GOLDEN_IDS)
def test_a_rubric_renders_the_bytes_the_contract_names(rubric: str, expected: str) -> None:
    assert render(rubric) == expected
    # The value itself stays the bare text: a rubric only expands where it becomes
    # wire text, so a member's value reads as it is written.
    assert str(rubric) in {BILLING, TECHNICAL, COSMETIC}


@given(st.text(min_size=1).filter(lambda text: bool(text.strip())))
def test_a_rubric_with_no_parts_renders_byte_for_byte(what: str) -> None:
    # The load-bearing invariant: 0.1.0's bytes do not move. A bare string and an
    # `option(...)` with nothing attached both render to the text itself.
    assert render(what) == what
    assert render(option(what)) == what
    assert render(level(what)) == what
    assert render(fallback(what)) == what


class Department(Choice):
    """A choice whose every member carries examples, one of them the fallback."""

    billing = option(
        BILLING,
        examples=["My card was charged twice", "Where is my refund?"],
        counterexamples=["The dashboard is down"],
    )
    technical = option(TECHNICAL, examples=["502 on every request"])
    sales = fallback("Pricing, upgrades, new accounts", examples=["Do you have a team plan?"])


class Severity(Levels):
    """Ordered levels, each with the inputs that score there."""

    cosmetic = level(COSMETIC, examples=["typo in a label"])
    degraded = level("Broken feature, workaround exists", examples=["export fails in one browser"])
    blocking = level("No workaround exists", examples=["cannot log in", "data loss"])


ANSWERS: dict[str, Json] = {
    "q0": {
        "type": "choice",
        "choice": "technical",
        "probabilities": {"billing": 0.1, "technical": 0.8, "sales": 0.1},
        "confidence": 0.9,
    },
    "q1": {
        "type": "score",
        "score": 1.0,
        "legend": {"0": COSMETIC, "1": "Broken feature", "2": "No workaround"},
        "probabilities": {"0": 0.1, "1": 0.8, "2": 0.1},
        "confidence": 0.9,
    },
}
"""One answer per question of the batch below, over its own keys and levels."""

CHOICE_CRITERIA: Json = {
    "billing": (
        f"{BILLING}\nExamples: My card was charged twice; Where is my refund?"
        f"\nNot this option: The dashboard is down"
    ),
    "technical": f"{TECHNICAL}\nExamples: 502 on every request",
    "sales": "Pricing, upgrades, new accounts\nExamples: Do you have a team plan?",
}
"""What `Department` must put on the wire, key by key."""

SCORE_CRITERIA: Json = [
    f"{COSMETIC}\nExamples: typo in a label",
    "Broken feature, workaround exists\nExamples: export fails in one browser",
    "No workaround exists\nExamples: cannot log in; data loss",
]
"""What `Severity` must put on the wire, low to high."""


def test_examples_reach_the_wire_as_the_rendered_criteria(
    httpserver: HTTPServer, runner: Runner
) -> None:
    expect_post(httpserver).respond_with_data(reply(ANSWERS), content_type=JSON)
    batch = (
        choose(Department, "Which team should handle this?"),
        score(Severity, "How bad is it?"),
    )
    assert runner.ask(batch, TICKET) == (Department.technical, Severity.degraded)

    request, _ = httpserver.log[-1]
    body = as_object(narrow(request.get_json()))
    check_request(body)
    questions = as_object(body["questions"])
    assert as_object(questions["q0"])["criteria"] == CHOICE_CRITERIA
    assert as_object(questions["q1"])["criteria"] == SCORE_CRITERIA
    # A fallback marked with examples is still the fallback.
    assert Department.fallback_member() is Department.sales
