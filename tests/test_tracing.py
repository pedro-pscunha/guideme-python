import json

import pytest
from opentelemetry.trace import SpanKind, StatusCode
from pytest_httpserver import HTTPServer

from guideme import AuthError, Policy
from guideme.api.client import EVALUATE
from guideme.question import noul
from guideme.telemetry import ANSWER_EVENT, ASK_SPAN, OPERATION, PROVIDER, RETRY_EVENT

from .conftest import (
    BATCH_ANSWERS,
    JSON,
    MODEL,
    TICKET,
    Recorded,
    Runner,
    attributes,
    batch,
    expect_post,
    noul_reply,
    reply,
)

ATTEMPT_SPAN = f"POST {EVALUATE}"
UNAUTHORIZED = "unauthorized: missing or invalid TypeSafe API key"
"""The `guideme.ask` status description a 401 must carry, spelled out."""


def test_one_ask_span_carries_the_documented_fields_with_a_child_and_an_event_each(
    httpserver: HTTPServer, runner: Runner, spans: Recorded
) -> None:
    expect_post(httpserver).respond_with_data(reply(BATCH_ANSWERS), content_type=JSON)
    _ = runner.ask(batch(), TICKET, lambda builder: builder.policy(Policy(min_confidence=0.6)))

    ask = spans.one(ASK_SPAN)
    assert ask.kind is SpanKind.CLIENT
    assert ask.parent is None
    assert attributes(ask) == {
        "gen_ai.provider.name": PROVIDER,
        "gen_ai.operation.name": OPERATION,
        "gen_ai.request.model": "jev-latest",
        "gen_ai.response.model": MODEL,
        "gen_ai.usage.input_tokens": 296,
        "gen_ai.usage.output_tokens": 20,
        "server.address": "localhost",
        "server.port": httpserver.port,
        "guideme.questions": 3,
        "guideme.state.bytes": len(json.dumps(TICKET).encode()),
    }
    assert ask.status.status_code is StatusCode.UNSET

    attempt = spans.one(ATTEMPT_SPAN)
    assert attempt.kind is SpanKind.CLIENT
    assert attempt.parent is not None
    assert ask.context is not None
    assert attempt.parent.span_id == ask.context.span_id
    assert attributes(attempt) == {
        "http.request.method": "POST",
        "server.address": "localhost",
        "server.port": httpserver.port,
        "url.full": f"{httpserver.url_for('')[:-1]}{EVALUATE}",
        "url.template": EVALUATE,
        "http.response.status_code": 200,
    }

    answers = spans.events(ask, ANSWER_EVENT)
    assert [attributes(answer) for answer in answers] == [
        {
            "guideme.question": "q0",
            "guideme.kind": "noul",
            "guideme.outcome": "yes",
            "guideme.probability": 0.95,
            "guideme.unsure": False,
            "guideme.yes_above": 0.5,
            "guideme.no_below": 0.5,
            "guideme.min_confidence": 0.6,
        },
        {
            "guideme.question": "q1",
            "guideme.kind": "choice",
            "guideme.outcome": "billing",
            "guideme.confidence": 0.81,
            "guideme.unsure": False,
            "guideme.yes_above": 0.5,
            "guideme.no_below": 0.5,
            "guideme.min_confidence": 0.6,
        },
        {
            "guideme.question": "q2",
            "guideme.kind": "score",
            "guideme.outcome": 1,
            "guideme.value": 0.95,
            "guideme.confidence": 0.92,
            "guideme.unsure": False,
            "guideme.yes_above": 0.5,
            "guideme.no_below": 0.5,
            "guideme.min_confidence": 0.6,
        },
    ]
    assert not [event for span in spans.all() for event in span.events if event.name == "exception"]


def test_a_failed_ask_marks_the_error_type_and_status_and_records_no_exception(
    httpserver: HTTPServer, runner: Runner, spans: Recorded
) -> None:
    expect_post(httpserver).respond_with_data("", status=401)
    with pytest.raises(AuthError):
        _ = runner.ask(noul("Urgent?"), TICKET)

    ask = spans.one(ASK_SPAN)
    assert attributes(ask)["error.type"] == "auth"
    assert ask.status.status_code is StatusCode.ERROR
    assert ask.status.description == AuthError().describe()
    assert ask.status.description == UNAUTHORIZED
    assert "gen_ai.response.model" not in attributes(ask)

    attempt = spans.one(ATTEMPT_SPAN)
    assert attributes(attempt)["error.type"] == "401"
    assert attempt.status.status_code is StatusCode.ERROR
    assert not [event for span in spans.all() for event in span.events]


def test_a_retry_emits_the_event_and_a_sibling_attempt_span(
    httpserver: HTTPServer, runner: Runner, spans: Recorded
) -> None:
    httpserver.expect_oneshot_request(EVALUATE, method="POST").respond_with_data(
        "", status=429, headers={"retry-after": "0"}
    )
    expect_post(httpserver).respond_with_data(noul_reply(0.95), content_type=JSON)
    assert runner.ask(noul("Urgent?"), TICKET) is True

    attempts = spans.named(ATTEMPT_SPAN)
    assert len(attempts) == 2
    throttled, succeeded = attempts
    assert attributes(throttled)["http.response.status_code"] == 429
    assert attributes(throttled)["error.type"] == "429"
    assert "http.request.resend_count" not in attributes(throttled)
    assert attributes(succeeded)["http.request.resend_count"] == 1
    assert attributes(succeeded)["http.response.status_code"] == 200
    assert "error.type" not in attributes(succeeded)

    retries = spans.events(throttled, RETRY_EVENT)
    assert len(retries) == 1
    assert attributes(retries[0]) == {
        "http.response.status_code": 429,
        "guideme.retry.attempt": 1,
        "guideme.retry.delay_ms": 0,
    }


def test_the_state_is_recorded_only_when_the_guide_was_asked_to(
    httpserver: HTTPServer, runner: Runner, spans: Recorded
) -> None:
    expect_post(httpserver).respond_with_data(noul_reply(0.95), content_type=JSON)
    state = {"ticket": TICKET, "plan": "pro"}
    compact = json.dumps(state, ensure_ascii=False, separators=(",", ":"))

    _ = runner.ask(noul("Urgent?"), state)
    silent = attributes(spans.one(ASK_SPAN))
    assert "guideme.state" not in silent
    assert silent["guideme.state.bytes"] == len(compact.encode())

    spans.reset()
    _ = runner.ask(noul("Urgent?"), state, lambda builder: builder.record_state(True))
    recorded = attributes(spans.one(ASK_SPAN))
    assert recorded["guideme.state"] == compact
    assert recorded["guideme.state.bytes"] == len(compact.encode())
