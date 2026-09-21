import json

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from guideme import Policy
from guideme.api import Request, Response, question_to_wire, request_to_wire
from guideme.ask import Plan, encode
from guideme.question import choose_among, noul, score_levels

from .conftest import FIXTURES, WIRE_ANSWER, Json, load_json, validator

check_request = validator("request")
check_response = validator("response")
EXAMPLES = ("noul", "choice", "score")


def _structured(question: str, datum: str) -> Json:
    return {"question": question, "data": [datum]}


def _described(instructions: Json, yes: str, no: str) -> object:
    return noul(instructions).criteria(yes, no)


def _json_values() -> st.SearchStrategy[Json]:
    text = st.text(min_size=1, max_size=20)
    return st.one_of(text, st.builds(_structured, text, text))


def _questions() -> st.SearchStrategy[object]:
    text = st.text(min_size=1, max_size=12)
    keys = st.text(alphabet="abcdefgh", min_size=1, max_size=4)
    return st.one_of(
        st.builds(noul, _json_values()),
        st.builds(_described, _json_values(), text, text),
        st.builds(
            choose_among,
            _json_values(),
            st.dictionaries(keys, st.none() | text, min_size=1, max_size=5),
        ),
        st.builds(score_levels, _json_values(), st.lists(text, min_size=2, max_size=10)),
    )


@st.composite
def _requests(draw: st.DrawFn) -> Request:
    batch = draw(st.lists(_questions(), min_size=1, max_size=4))
    plan = Plan(base=Policy())
    _ = encode(batch, plan)
    wire = {
        qid: question_to_wire(instructions, spec)
        for qid, (instructions, spec) in plan.specs.items()
    }
    return request_to_wire(draw(_json_values()), "jev-latest", wire)


@pytest.mark.parametrize("example", EXAMPLES)
def test_each_docs_example_matches_the_schema_and_survives_a_round_trip(example: str) -> None:
    body = load_json(FIXTURES / f"{example}.json")
    check_response(body)
    parsed = Response.model_validate_json(json.dumps(body))
    assert parsed.model_dump(by_alias=True) == body
    assert parsed.model == "jev-1.13.0"
    assert parsed.usage.input_tokens > 0


@given(
    outside=st.floats(allow_nan=False, allow_infinity=False).filter(
        lambda value: not 0.0 <= value <= 1.0
    )
)
def test_a_probability_or_confidence_outside_the_unit_is_refused_at_parse_time(
    outside: float,
) -> None:
    wrong_noul = json.dumps({"type": "noul", "noul": outside})
    wrong_confidence = json.dumps(
        {"type": "choice", "choice": "a", "probabilities": {"a": 1.0}, "confidence": outside}
    )
    for body in (wrong_noul, wrong_confidence):
        with pytest.raises(ValidationError):
            _ = WIRE_ANSWER.validate_json(body)


@given(request=_requests())
def test_every_request_guideme_builds_matches_the_schema_and_is_keyed_q0_to_qn(
    request: Request,
) -> None:
    body = request.model_dump(by_alias=True)
    check_request(body)
    assert list(request.questions) == [f"q{index}" for index in range(len(request.questions))]
    assert Request.model_validate_json(request.model_dump_json(by_alias=True)) == request
