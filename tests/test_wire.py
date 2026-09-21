import json
import time
from collections.abc import Mapping

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError
from pytest_httpserver import HTTPServer
from pytest_httpserver.httpserver import RequestHandler

from guideme import (
    AuthError,
    ConfigError,
    InvalidError,
    Key,
    OverloadedError,
    Policy,
    ProtocolError,
    Rank,
    UnsureError,
)
from guideme.api import Request, Response, question_to_wire, request_to_wire
from guideme.api.client import EVALUATE
from guideme.ask import Plan, encode
from guideme.question import Question, choose_among, noul, score_levels

from .conftest import (
    FIXTURES,
    TEST_KEY,
    WIRE_ANSWER,
    Json,
    Runner,
    as_object,
    load_json,
    narrow,
    validator,
)

MODEL = "jev-1.13.0"
JSON = "application/json"
TICKET = "Help! My payouts have been failing for 3 days."

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


def reply(answers: Mapping[str, Json], *, model: str = MODEL) -> str:
    """A `POST /v1/systemone` body the local httpserver can hand back."""
    return json.dumps(
        {
            "model": model,
            "answers": dict(answers),
            "usage": {"input_tokens": 296, "output_tokens": 20},
        }
    )


def noul_reply(probability: float) -> str:
    return reply({"q0": {"type": "noul", "noul": probability}})


def expect_post(httpserver: HTTPServer) -> RequestHandler:
    return httpserver.expect_request(EVALUATE, method="POST")


def test_a_401_is_an_auth_error_and_is_not_retried(httpserver: HTTPServer, runner: Runner) -> None:
    expect_post(httpserver).respond_with_data("", status=401)
    with pytest.raises(AuthError):
        _ = runner.ask(noul("Urgent?"), TICKET)
    assert len(httpserver.log) == 1


def test_a_422_is_an_invalid_error_carrying_the_body(
    httpserver: HTTPServer, runner: Runner
) -> None:
    detail = '{"detail":"questions.q0.criteria: must not be empty"}'
    expect_post(httpserver).respond_with_data(detail, status=422)
    with pytest.raises(InvalidError) as raised:
        _ = runner.ask(noul("Urgent?"), TICKET)
    assert raised.value.detail == detail
    assert raised.value.kind == "invalid"
    assert len(httpserver.log) == 1


def test_a_429_is_retried_after_the_advertised_delay(
    httpserver: HTTPServer, runner: Runner
) -> None:
    httpserver.expect_oneshot_request(EVALUATE, method="POST").respond_with_data(
        "", status=429, headers={"retry-after": "1"}
    )
    expect_post(httpserver).respond_with_data(noul_reply(0.95), content_type=JSON)
    started = time.monotonic()
    assert runner.ask(noul("Urgent?"), TICKET) is True
    assert time.monotonic() - started >= 1.0
    assert len(httpserver.log) == 2


def test_a_529_exhausts_the_retries_and_then_reports_overloaded(
    httpserver: HTTPServer, runner: Runner
) -> None:
    expect_post(httpserver).respond_with_data("", status=529)
    with pytest.raises(OverloadedError):
        _ = runner.ask(noul("Urgent?"), TICKET, lambda builder: builder.max_retries(2))
    assert len(httpserver.log) == 3


def test_a_body_that_violates_the_contract_is_a_protocol_error(
    httpserver: HTTPServer, runner: Runner
) -> None:
    expect_post(httpserver).respond_with_data(noul_reply(1.5), content_type=JSON)
    with pytest.raises(ProtocolError) as raised:
        _ = runner.ask(noul("Urgent?"), TICKET, lambda builder: builder.max_retries(0))
    assert raised.value.kind == "protocol"
    assert len(httpserver.log) == 1


def test_an_option_outside_the_rubric_is_a_protocol_error(
    httpserver: HTTPServer, runner: Runner
) -> None:
    answer: Json = {
        "type": "choice",
        "choice": "ghost",
        "probabilities": {"billing": 0.1, "ghost": 0.9},
        "confidence": 0.9,
    }
    expect_post(httpserver).respond_with_data(reply({"q0": answer}), content_type=JSON)
    question = choose_among("Which team?", {"billing": None, "sales": None})
    with pytest.raises(ProtocolError) as raised:
        _ = runner.ask(question, TICKET)
    assert "ghost" in raised.value.detail


def test_an_unsure_answer_with_no_fallback_names_the_question(
    httpserver: HTTPServer, runner: Runner
) -> None:
    expect_post(httpserver).respond_with_data(noul_reply(0.95), content_type=JSON)
    with pytest.raises(UnsureError) as raised:
        _ = runner.ask(noul("Urgent?").yes_above(0.99), TICKET)
    assert raised.value.question == "q0"
    assert raised.value.value == 0.95
    assert raised.value.threshold == 0.99


def test_an_empty_batch_is_refused_before_any_request(
    httpserver: HTTPServer, runner: Runner
) -> None:
    expect_post(httpserver).respond_with_data(noul_reply(0.95), content_type=JSON)
    empty: list[Question[bool]] = []
    with pytest.raises(ConfigError):
        _ = runner.ask(empty, TICKET)
    assert not httpserver.log


def test_the_request_carries_the_bearer_token_and_matches_the_schema(
    httpserver: HTTPServer, runner: Runner
) -> None:
    answers: dict[str, Json] = {
        "q0": {"type": "noul", "noul": 0.95},
        "q1": {
            "type": "choice",
            "choice": "billing",
            "probabilities": {"billing": 0.88, "sales": 0.12},
            "confidence": 0.81,
        },
        "q2": {
            "type": "score",
            "score": 0.95,
            "legend": {"0": "Calm", "1": "Cross"},
            "probabilities": {"0": 0.05, "1": 0.95},
            "confidence": 0.92,
        },
    }
    expect_post(httpserver).respond_with_data(reply(answers), content_type=JSON)
    batch = (
        noul("Urgent?").criteria("needs a person now", "can wait"),
        choose_among("Which team?", {"billing": "Money", "sales": None}),
        score_levels("How cross?", ["Calm", "Cross"]),
    )
    assert runner.ask(batch, TICKET) == (True, Key("billing"), Rank(1))

    request, _ = httpserver.log[0]
    assert request.headers["authorization"] == f"Bearer {TEST_KEY}"
    assert request.headers["content-type"] == JSON
    body = as_object(narrow(request.get_json()))
    check_request(body)
    assert list(as_object(body["questions"])) == ["q0", "q1", "q2"]
    assert body["state"] == TICKET
