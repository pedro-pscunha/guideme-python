import copy
import pickle
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pytest_httpserver import HTTPServer

from guideme import Choice, Levels, choose, fallback, level, noul, option, score
from guideme.enums import render

from .conftest import (
    JSON,
    REPO_ROOT,
    TICKET,
    Json,
    Runner,
    as_list,
    as_object,
    as_str,
    expect_post,
    load_json,
    narrow,
    reply,
    validator,
)

check_request = validator("request")

BILLING = "Payments, invoicing, refunds"
TECHNICAL = "Bugs, outages, integrations"
COSMETIC = "No impact to functionality"

VECTOR_PATH = REPO_ROOT / "spec" / "vectors" / "rubric.json"


def _declared(raw: Json) -> tuple[str, str, str]:
    """One vector case as the rubric it declares, its bare text, and its expected bytes.

    `kind` is what picks the constructor, which is what that field is for: a `levels`
    case has to go through `level(...)`, and a `choice` or `noul` case through
    `option(...)`, so the vector exercises the constructors a caller writes rather than
    `render` alone.

    An absent clause arrives as `[]` because that is how the generator serialises an
    empty `Vec`, and it is mapped back to "no argument". Passing `[]` through would be
    a different declaration: these constructors refuse an empty clause written out.
    """
    case = as_object(raw)
    what = as_str(case["what"])
    examples = [as_str(item) for item in as_list(case["examples"])] or None
    counterexamples = [as_str(item) for item in as_list(case["counterexamples"])] or None
    kind = as_str(case["kind"])
    if kind == "levels":
        declared = level(what, examples=examples)
    elif kind in {"choice", "noul"}:
        declared = option(what, examples=examples, counterexamples=counterexamples)
    else:
        message = f"{VECTOR_PATH}: unknown kind {kind!r}"
        raise AssertionError(message)
    return declared, what, as_str(case["rendered"])


def _cases(path: Path) -> list[Json]:
    """The vector's cases, refusing an empty file.

    An empty parametrisation is a test that passes without ever running, so a vector
    that arrived empty would silence the drift this file exists to catch.
    """
    cases = as_list(load_json(path))
    if not cases:
        message = f"{path} carries no cases"
        raise AssertionError(message)
    return cases


CASES = _cases(VECTOR_PATH)
VECTOR = [_declared(raw) for raw in CASES]
VECTOR_IDS = [f"{index}_{as_str(as_object(raw)['kind'])}" for index, raw in enumerate(CASES)]


@pytest.mark.parametrize(("rubric", "bare", "expected"), VECTOR, ids=VECTOR_IDS)
def test_a_rubric_renders_the_bytes_the_contract_names(
    rubric: str, bare: str, expected: str
) -> None:
    assert render(rubric) == expected
    # The value itself stays the bare text: a rubric only expands where it becomes
    # wire text, so a member's value reads exactly as it was written.
    assert str(rubric) == bare
    # And it survives the three ways a value gets duplicated. `__new__` takes four
    # arguments where `str` hands back one, so each of these raised a `TypeError`
    # until the rubric said how to rebuild itself.
    for clone in (copy.copy(rubric), copy.deepcopy(rubric), pickle.loads(pickle.dumps(rubric))):  # noqa: S301 -- the payload is this test's own value
        assert render(clone) == expected


@given(st.text())
def test_a_rubric_with_no_parts_renders_byte_for_byte(what: str) -> None:
    # The load-bearing invariant: 0.1.0's bytes do not move. A bare string and an
    # `option(...)` with nothing attached both render to the text itself.
    #
    # The strategy is unrestricted on purpose, blank and whitespace-only text
    # included. A rubric carrying no examples is refused for nothing at all: it
    # meant whatever it meant in 0.1.0 and a patch release does not redefine it.
    # Attaching examples to a blank rubric is the error, and that case is in
    # `tests/test_enums.py`'s refusal list.
    assert render(what) == what
    assert render(option(what)) == what
    assert render(level(what)) == what
    assert render(fallback(what)) == what


DASHBOARD = "The dashboard is down"
"""An example of one option and a counterexample of another: the confusable pattern."""


class Department(Choice):
    """A choice whose every member carries examples, one of them the fallback."""

    billing = option(
        BILLING,
        examples=["My card was charged twice", "Where is my refund?"],
        counterexamples=[DASHBOARD],
    )
    technical = option(TECHNICAL, examples=[DASHBOARD, "502 on every request"])
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
    "q2": {"type": "noul", "noul": 0.2},
}
"""One answer per question of the batch below, over its own keys and levels."""

CHOICE_CRITERIA: Json = {
    "billing": (
        f"{BILLING}\nExamples: My card was charged twice; Where is my refund?"
        f"\nNot this option: {DASHBOARD}"
    ),
    "technical": f"{TECHNICAL}\nExamples: {DASHBOARD}; 502 on every request",
    "sales": "Pricing, upgrades, new accounts\nExamples: Do you have a team plan?",
}
"""What `Department` must put on the wire, key by key."""

SCORE_CRITERIA: Json = [
    f"{COSMETIC}\nExamples: typo in a label",
    "Broken feature, workaround exists\nExamples: export fails in one browser",
    "No workaround exists\nExamples: cannot log in; data loss",
]
"""What `Severity` must put on the wire, low to high."""

NOUL_CRITERIA: Json = {
    "true": "Needs a person now\nExamples: the whole site is down",
    "false": "Can wait\nExamples: a broken job someone has a manual workaround for",
}
"""What a noul's described criteria must put on the wire, under the wire's own names."""


def test_examples_reach_the_wire_as_the_rendered_criteria(
    httpserver: HTTPServer, runner: Runner
) -> None:
    expect_post(httpserver).respond_with_data(reply(ANSWERS), content_type=JSON)
    batch = (
        choose(Department, "Which team should handle this?"),
        score(Severity, "How bad is it?"),
        noul("Is this urgent?").criteria(
            option("Needs a person now", examples=["the whole site is down"]),
            option("Can wait", examples=["a broken job someone has a manual workaround for"]),
        ),
    )
    assert runner.ask(batch, TICKET) == (Department.technical, Severity.degraded, False)

    request, _ = httpserver.log[-1]
    body = as_object(narrow(request.get_json()))
    check_request(body)
    questions = as_object(body["questions"])
    assert as_object(questions["q0"])["criteria"] == CHOICE_CRITERIA
    assert as_object(questions["q1"])["criteria"] == SCORE_CRITERIA
    assert as_object(questions["q2"])["criteria"] == NOUL_CRITERIA
    # A fallback marked with examples is still the fallback, and `The dashboard is down`
    # went out as an example of one option and a counterexample of another.
    assert Department.fallback_member() is Department.sales
