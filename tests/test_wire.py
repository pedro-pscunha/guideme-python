import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import final

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError
from pytest_httpserver import HTTPServer

from guideme import (
    ApiKey,
    AuthError,
    Choice,
    Confidence,
    ConfigError,
    Guide,
    GuideBuilder,
    GuidemeError,
    InvalidError,
    Key,
    Levels,
    Model,
    OverloadedError,
    Policy,
    Probability,
    ProtocolError,
    Rank,
    Ranked,
    RateLimitedError,
    TransportError,
    UnexpectedStatusError,
    UnsureError,
    choose,
    fallback,
    score,
)
from guideme.api import NoulAnswer, Request, Response, question_to_wire, request_to_wire
from guideme.api.client import EVALUATE, MODELS
from guideme.ask import Plan, encode
from guideme.guide import KEY_VAR, ModelInfo
from guideme.question import Question, choose_among, noul, score_levels
from guideme.telemetry import ASK_SPAN

from .conftest import (
    FIXTURES,
    JSON,
    MODEL,
    TEST_KEY,
    TICKET,
    WIRE_ANSWER,
    Configure,
    Json,
    Recorded,
    Runner,
    as_object,
    async_entry,
    attributes,
    closed_port,
    configured,
    expect_post,
    load_json,
    narrow,
    noul_reply,
    reply,
    validator,
)

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
    assert parsed.model == MODEL


@given(
    outside=st.floats(allow_nan=False, allow_infinity=False).filter(
        lambda value: not 0.0 <= value <= 1.0
    ),
    inside=st.floats(min_value=0.0, max_value=1.0),
)
def test_a_probability_or_confidence_is_refused_outside_the_unit_and_kept_inside(
    outside: float, inside: float
) -> None:
    wrong_noul = json.dumps({"type": "noul", "noul": outside})
    wrong_confidence = json.dumps(
        {"type": "choice", "choice": "a", "probabilities": {"a": 1.0}, "confidence": outside}
    )
    for body in (wrong_noul, wrong_confidence):
        with pytest.raises(ValidationError):
            _ = WIRE_ANSWER.validate_json(body)
    for value in (inside, 0.0, 1.0):
        parsed = WIRE_ANSWER.validate_json(json.dumps({"type": "noul", "noul": value}))
        assert isinstance(parsed, NoulAnswer)
        assert parsed.noul == value


@given(request=_requests())
def test_every_request_guideme_builds_matches_the_schema_and_is_keyed_q0_to_qn(
    request: Request,
) -> None:
    body = request.model_dump(by_alias=True)
    check_request(body)
    assert list(request.questions) == [f"q{index}" for index in range(len(request.questions))]
    assert Request.model_validate_json(request.model_dump_json(by_alias=True)) == request


RETRIES = 1
"""Retries each failing-status case allows, so an exhausted one is exactly two requests."""

DETAIL = '{"detail":"questions.q0.criteria: must not be empty"}'
"""A 422 body, which the error must carry verbatim and the span must not."""

type Serve = Callable[[HTTPServer], Configure | None]
"""How one failure case arranges the server, returning any builder change it needs."""

type Check = Callable[[GuidemeError], None]
"""What one failure case asserts about the raised error beyond its class and kind."""


def _status(status: int, body: str = "", retry_after: str | None = None) -> Serve:
    headers = None if retry_after is None else {"retry-after": retry_after}

    def serve(httpserver: HTTPServer) -> Configure | None:
        expect_post(httpserver).respond_with_data(body, status=status, headers=headers)

    return serve


def _refused(_httpserver: HTTPServer) -> Configure | None:
    return lambda builder: builder.base_url(f"http://127.0.0.1:{closed_port()}")


def _nothing_more(_error: GuidemeError) -> None:
    """The class and the kind are the whole contract for this status."""


def _carries_the_body(error: GuidemeError) -> None:
    assert isinstance(error, InvalidError)
    assert error.detail == DETAIL


def _parsed_the_retry_after(error: GuidemeError) -> None:
    assert isinstance(error, RateLimitedError)
    assert error.retry_after == timedelta(seconds=0)


@final
@dataclass(frozen=True, slots=True)
class Failure:
    """One failing call: how the server behaves, and everything the caller must see."""

    serve: Serve
    expected: type[GuidemeError]
    kind: str
    served: int
    check: Check


FAILURES = [
    Failure(_status(401), AuthError, "auth", 1, _nothing_more),
    Failure(_status(422, body=DETAIL), InvalidError, "invalid", 1, _carries_the_body),
    Failure(
        _status(429, retry_after="0"),
        RateLimitedError,
        "rate_limited",
        RETRIES + 1,
        _parsed_the_retry_after,
    ),
    Failure(
        _status(500, body="upstream exploded"),
        UnexpectedStatusError,
        "unexpected_status",
        1,
        _nothing_more,
    ),
    Failure(_status(529), OverloadedError, "overloaded", RETRIES + 1, _nothing_more),
    Failure(_refused, TransportError, "transport", 0, _nothing_more),
]


@pytest.mark.parametrize("failure", FAILURES, ids=[case.kind for case in FAILURES])
def test_every_failure_raises_its_typed_error_and_marks_the_ask_span(
    httpserver: HTTPServer, runner: Runner, spans: Recorded, failure: Failure
) -> None:
    extra = failure.serve(httpserver)

    def configure(builder: GuideBuilder) -> GuideBuilder:
        settled = builder.max_retries(RETRIES)
        return settled if extra is None else extra(settled)

    with pytest.raises(failure.expected) as raised:
        _ = runner.ask(noul("Urgent?"), TICKET, configure)
    assert raised.value.kind == failure.kind
    failure.check(raised.value)
    assert len(httpserver.log) == failure.served
    assert attributes(spans.one(ASK_SPAN))["error.type"] == failure.kind


BACKOFF = timedelta(milliseconds=300)
"""Long enough to measure that a retry really waited, short enough to pay for twice."""


def test_a_429_is_retried_after_waiting_out_the_backoff(
    httpserver: HTTPServer, runner: Runner
) -> None:
    httpserver.expect_oneshot_request(EVALUATE, method="POST").respond_with_data("", status=429)
    expect_post(httpserver).respond_with_data(noul_reply(0.95), content_type=JSON)
    started = time.monotonic()
    assert runner.ask(noul("Urgent?"), TICKET, lambda builder: builder.backoff(BACKOFF)) is True
    assert time.monotonic() - started >= BACKOFF.total_seconds()
    assert len(httpserver.log) == 2


CONCURRENT = 2
"""Asks issued at once, which must wait out their retries together rather than in turn."""

ADVERTISED = 1.0
"""Seconds the server puts in `retry-after`; whole seconds are all the header expresses."""

MARGIN = 0.2
"""Slack below the sequential time, so the assertion fails on serialisation, not on load."""


async def _two_asks(base_url: str) -> list[object]:
    guide = configured(base_url).build_async()
    try:
        entry = async_entry(guide)
        return list(
            await asyncio.gather(entry(noul("Urgent?"), TICKET), entry(noul("Urgent?"), TICKET))
        )
    finally:
        await guide.close()


def test_two_async_asks_wait_out_their_retries_at_the_same_time(httpserver: HTTPServer) -> None:
    for _ in range(CONCURRENT):
        httpserver.expect_oneshot_request(EVALUATE, method="POST").respond_with_data(
            "", status=429, headers={"retry-after": "1"}
        )
    expect_post(httpserver).respond_with_data(noul_reply(0.95), content_type=JSON)

    started = time.monotonic()
    assert asyncio.run(_two_asks(httpserver.url_for(""))) == [True, True]
    elapsed = time.monotonic() - started

    assert elapsed >= ADVERTISED
    assert elapsed < CONCURRENT * ADVERTISED - MARGIN
    assert len(httpserver.log) == 2 * CONCURRENT


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


class Department(Choice):
    """The README's rubric, whose `sales` member is the marked fallback."""

    billing = "Payments, invoicing, refunds"
    technical = "Bugs, outages, integrations"
    sales = fallback("Pricing, upgrades, new accounts")


class Frustration(Levels):
    """The README's levels, in order."""

    calm = "Calm and polite"
    frustrated = "Frustrated"
    very_angry = "Very angry"


FLOOR = 0.6
"""A confidence floor every unsure answer below sits under."""

UNSURE_CHOICE: Json = {
    "type": "choice",
    "choice": "billing",
    "probabilities": {"billing": 0.4, "technical": 0.35, "sales": 0.25},
    "confidence": 0.2,
}
"""A choice answer the policy calls unsure, over `Department`'s three keys."""

UNSURE_SCORE: Json = {
    "type": "score",
    "score": 1.1,
    "legend": {"0": "Calm and polite", "1": "Frustrated", "2": "Very angry"},
    "probabilities": {"0": 0.2, "1": 0.5, "2": 0.3},
    "confidence": 0.2,
}
"""A score answer the policy calls unsure, over `Frustration`'s three levels."""

RANKED_UNSURE = Ranked(
    choice=Department.billing,
    confidence=Confidence(0.2),
    unsure=True,
    probabilities=(
        (Department.billing, Probability(0.4)),
        (Department.technical, Probability(0.35)),
        (Department.sales, Probability(0.25)),
    ),
)
"""`UNSURE_CHOICE` as `.detail()` reads it: the reading handed back instead of a failure."""


def _noul_otherwise() -> object:
    return noul("Urgent?").yes_above(0.99).otherwise(False)


def _choice_fallback() -> object:
    return choose(Department, "Which team?").min_confidence(FLOOR)


def _choice_otherwise() -> object:
    return choose(Department, "Which team?").min_confidence(FLOOR).otherwise(Department.technical)


def _choice_detail() -> object:
    return choose(Department, "Which team?").min_confidence(FLOOR).detail()


def _score_otherwise() -> object:
    return score(Frustration, "How frustrated?").min_confidence(FLOOR).otherwise(Frustration.calm)


LADDER: list[tuple[dict[str, Json], Callable[[], object], object]] = [
    ({"q0": {"type": "noul", "noul": 0.95}}, _noul_otherwise, False),
    ({"q0": UNSURE_CHOICE}, _choice_fallback, Department.sales),
    ({"q0": UNSURE_CHOICE}, _choice_otherwise, Department.technical),
    ({"q0": UNSURE_CHOICE}, _choice_detail, RANKED_UNSURE),
    ({"q0": UNSURE_SCORE}, _score_otherwise, Frustration.calm),
]

LADDER_IDS = [
    "noul_otherwise",
    "choice_falls_back_to_the_marked_member",
    "choice_otherwise_beats_the_marked_member",
    "choice_detail_never_fails",
    "score_otherwise",
]


@pytest.mark.parametrize(("answers", "build", "expected"), LADDER, ids=LADDER_IDS)
def test_the_unsure_ladder_resolves_in_order(
    httpserver: HTTPServer,
    runner: Runner,
    answers: dict[str, Json],
    build: Callable[[], object],
    expected: object,
) -> None:
    expect_post(httpserver).respond_with_data(reply(answers), content_type=JSON)
    assert runner.ask(build(), TICKET) == expected


def test_a_batch_is_atomic_so_one_unsure_answer_fails_the_whole_call(
    httpserver: HTTPServer, runner: Runner
) -> None:
    answers: dict[str, Json] = {
        "q0": {"type": "noul", "noul": 0.95},
        "q1": UNSURE_CHOICE,
        "q2": {
            "type": "score",
            "score": 0.95,
            "legend": {"0": "Calm", "1": "Cross"},
            "probabilities": {"0": 0.05, "1": 0.95},
            "confidence": 0.92,
        },
    }
    expect_post(httpserver).respond_with_data(reply(answers), content_type=JSON)
    shape = (
        noul("Urgent?"),
        choose_among(
            "Which team?", {"billing": None, "technical": None, "sales": None}
        ).min_confidence(FLOOR),
        score_levels("How cross?", ["Calm", "Cross"]),
    )
    with pytest.raises(UnsureError) as raised:
        _ = runner.ask(shape, TICKET)
    assert raised.value.question == "q1"
    assert raised.value.value == 0.2
    assert raised.value.threshold == FLOOR
    assert len(httpserver.log) == 1


def _an_empty_batch() -> object:
    empty: list[Question[bool]] = []
    return empty


def _instructions_holding_a_nan() -> object:
    return noul({"x": float("nan")})


def _instructions_json_cannot_carry() -> object:
    # `bytes` is a `Sequence[int]` and so satisfies `Json`; `json.dumps` refuses it.
    return noul(b"not text")


UNSENDABLE: list[Callable[[], object]] = [
    _an_empty_batch,
    _instructions_holding_a_nan,
    _instructions_json_cannot_carry,
]

UNSENDABLE_IDS = ["empty_batch", "instructions_holding_a_nan", "instructions_json_cannot_carry"]


@pytest.mark.parametrize("build", UNSENDABLE, ids=UNSENDABLE_IDS)
def test_a_shape_the_wire_cannot_carry_is_refused_before_any_request(
    httpserver: HTTPServer, runner: Runner, build: Callable[[], object]
) -> None:
    expect_post(httpserver).respond_with_data(noul_reply(0.95), content_type=JSON)
    with pytest.raises(ConfigError):
        _ = runner.ask(build(), TICKET)
    assert not httpserver.log


CREDENTIALED = "https://user:sk-live-SENTINEL@host"
"""A base URL whose userinfo would land on every attempt span if it were accepted."""

SENTINEL = "SENTINEL"
"""The marker the refusal above must keep out of telemetry."""


def _a_credentialed_base_url(_monkeypatch: pytest.MonkeyPatch) -> None:
    _ = GuideBuilder().api_key(ApiKey("k")).base_url(CREDENTIALED).build()


def _an_empty_api_key(_monkeypatch: pytest.MonkeyPatch) -> None:
    _ = ApiKey("")


def _no_key_in_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(KEY_VAR, raising=False)
    _ = Guide.from_env()


def _an_api_key_of_spaces(_monkeypatch: pytest.MonkeyPatch) -> None:
    _ = ApiKey("   ")


def _an_empty_model(_monkeypatch: pytest.MonkeyPatch) -> None:
    _ = Model("")


def _levels_given_as_one_string(_monkeypatch: pytest.MonkeyPatch) -> None:
    # A `str` is a `Sequence[str]`, so the checker allows it and the scale would be
    # the three letters of "abc".
    _ = score_levels("How cross?", "abc")


@pytest.mark.parametrize(
    "build",
    [
        _a_credentialed_base_url,
        _an_empty_api_key,
        _no_key_in_the_environment,
        _an_api_key_of_spaces,
        _an_empty_model,
        _levels_given_as_one_string,
    ],
    ids=[
        "credentialed_base_url",
        "empty_api_key",
        "key_not_in_the_environment",
        "api_key_of_spaces",
        "empty_model",
        "levels_given_as_one_string",
    ],
)
def test_a_configuration_mistake_is_refused_before_a_guide_exists(
    monkeypatch: pytest.MonkeyPatch,
    spans: Recorded,
    build: Callable[[pytest.MonkeyPatch], None],
) -> None:
    with pytest.raises(ConfigError):
        build(monkeypatch)
    assert not spans.all()
    assert not [text for text in spans.texts() if SENTINEL in text]


ELSEWHERE = "/v1/systemone-elsewhere"
"""Where the redirect case points. Same origin, so `httpx` keeps the bearer on the hop."""


def _served_directly(httpserver: HTTPServer, body: str) -> None:
    expect_post(httpserver).respond_with_data(body, content_type=JSON)


def _served_after_a_redirect(httpserver: HTTPServer, body: str) -> None:
    expect_post(httpserver).respond_with_data("", status=307, headers={"location": ELSEWHERE})
    httpserver.expect_request(ELSEWHERE, method="POST").respond_with_data(body, content_type=JSON)


@pytest.mark.parametrize(
    ("arrange", "served"),
    [(_served_directly, 1), (_served_after_a_redirect, 2)],
    ids=["direct", "after_a_307"],
)
def test_the_request_carries_the_bearer_token_and_matches_the_schema(
    httpserver: HTTPServer,
    runner: Runner,
    arrange: Callable[[HTTPServer, str], None],
    served: int,
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
    arrange(httpserver, reply(answers))
    batch = (
        noul("Urgent?").criteria("needs a person now", "can wait"),
        choose_among("Which team?", {"billing": "Money", "sales": None}),
        score_levels("How cross?", ["Calm", "Cross"]),
    )
    assert runner.ask(batch, TICKET) == (True, Key("billing"), Rank(1))
    assert len(httpserver.log) == served

    request, _ = httpserver.log[-1]
    assert request.headers["authorization"] == f"Bearer {TEST_KEY}"
    assert request.headers["content-type"] == JSON
    body = as_object(narrow(request.get_json()))
    check_request(body)
    assert list(as_object(body["questions"])) == ["q0", "q1", "q2"]
    assert body["state"] == TICKET


MODELS_BODY: Json = {
    "models": [
        {
            "name": MODEL,
            "description": "The current stable Jev.",
            "release_date": "2026-02-11",
        },
        {
            "name": "jev-1.12.0",
            "description": "The Jev before it.",
            "release_date": "2025-11-04",
        },
    ]
}
"""A `GET /v1/models` body, as the docs describe one."""


def test_the_model_list_comes_back_as_values_under_its_own_span(
    httpserver: HTTPServer, runner: Runner, spans: Recorded
) -> None:
    httpserver.expect_request(MODELS, method="GET").respond_with_data(
        json.dumps(MODELS_BODY), content_type=JSON
    )
    assert runner.models() == (
        ModelInfo(name=MODEL, description="The current stable Jev.", release_date="2026-02-11"),
        ModelInfo(name="jev-1.12.0", description="The Jev before it.", release_date="2025-11-04"),
    )

    request, _ = httpserver.log[0]
    assert request.headers["authorization"] == f"Bearer {TEST_KEY}"
    assert not spans.named(ASK_SPAN)
    assert attributes(spans.one(f"GET {MODELS}")) == {
        "http.request.method": "GET",
        "server.address": "localhost",
        "server.port": httpserver.port,
        "url.full": f"{httpserver.url_for('')[:-1]}{MODELS}",
        "url.template": MODELS,
        "http.response.status_code": 200,
    }
