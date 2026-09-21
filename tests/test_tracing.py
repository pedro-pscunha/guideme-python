import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, cast, final

import pytest
from opentelemetry._logs import SeverityNumber
from opentelemetry.trace import SpanKind, StatusCode
from pytest_httpserver import HTTPServer

from guideme import AuthError, Policy, telemetry
from guideme.api.client import EVALUATE
from guideme.question import noul
from guideme.telemetry import ANSWER_EVENT, ASK_SPAN, OPERATION, PROVIDER, RETRY_EVENT, Events

from .conftest import (
    BATCH_ANSWERS,
    JSON,
    MODEL,
    TICKET,
    Logged,
    Recorded,
    Runner,
    attributes,
    batch,
    expect_post,
    log_attributes,
    log_scope,
    noul_reply,
    reply,
    without_the_logs_api,
)

if TYPE_CHECKING:
    from opentelemetry._logs import Logger

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


def test_a_failed_ask_marks_the_error_type_and_status_and_logs_nothing(
    httpserver: HTTPServer, runner: Runner, spans: Recorded, records: Logged
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
    assert not [
        record
        for record in records.all()
        if record.log_record.severity_number is SeverityNumber.ERROR
    ]
    assert not records.all()


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


def test_every_answer_is_also_a_log_record_at_info_carrying_the_ask_span_ids(
    httpserver: HTTPServer, runner: Runner, spans: Recorded, records: Logged
) -> None:
    expect_post(httpserver).respond_with_data(reply(BATCH_ANSWERS), content_type=JSON)
    _ = runner.ask(batch(), TICKET, lambda builder: builder.policy(Policy(min_confidence=0.6)))

    ask = spans.one(ASK_SPAN)
    assert ask.context is not None
    answers = records.named(ANSWER_EVENT)
    assert len(answers) == 3
    assert [record.log_record.body for record in answers] == [
        "q0 noul: yes",
        "q1 choice: billing",
        "q2 score: level 1",
    ]
    for record in answers:
        assert log_scope(record) == "guideme"
        assert record.log_record.severity_text == "INFO"
        assert record.log_record.severity_number is SeverityNumber.INFO
        assert record.log_record.trace_id == ask.context.trace_id
        assert record.log_record.span_id == ask.context.span_id
    assert log_attributes(answers[2]) == {
        "guideme.question": "q2",
        "guideme.kind": "score",
        "guideme.outcome": 1,
        "guideme.value": 0.95,
        "guideme.confidence": 0.92,
        "guideme.unsure": False,
        "guideme.yes_above": 0.5,
        "guideme.no_below": 0.5,
        "guideme.min_confidence": 0.6,
    }
    assert [log_attributes(record) for record in answers] == [
        attributes(event) for event in spans.events(ask, ANSWER_EVENT)
    ]


def test_a_retry_is_one_warn_record_carrying_the_attempt_span_ids(
    httpserver: HTTPServer, runner: Runner, spans: Recorded, records: Logged
) -> None:
    httpserver.expect_oneshot_request(EVALUATE, method="POST").respond_with_data(
        "", status=429, headers={"retry-after": "0"}
    )
    expect_post(httpserver).respond_with_data(noul_reply(0.95), content_type=JSON)
    assert runner.ask(noul("Urgent?"), TICKET) is True

    ask = spans.one(ASK_SPAN)
    throttled = spans.named(ATTEMPT_SPAN)[0]
    assert ask.context is not None
    assert throttled.context is not None

    retry = records.one(RETRY_EVENT)
    assert log_scope(retry) == "guideme.api"
    assert retry.log_record.severity_text == "WARN"
    assert retry.log_record.severity_number is SeverityNumber.WARN
    assert retry.log_record.body == "429 from TypeSafe, retrying in 0 ms"
    assert log_attributes(retry) == {
        "http.response.status_code": 429,
        "guideme.retry.attempt": 1,
        "guideme.retry.delay_ms": 0,
    }
    assert retry.log_record.trace_id == ask.context.trace_id
    assert retry.log_record.span_id == throttled.context.span_id
    assert retry.log_record.span_id != ask.context.span_id
    assert records.one(ANSWER_EVENT).log_record.span_id == ask.context.span_id


@final
@dataclass(frozen=True, slots=True)
class Route:
    """One `events(...)` mode, and which signal it writes an answer and a retry to."""

    mode: Events
    on_spans: bool
    as_records: bool
    logs_api: bool = True
    """Whether this run has an `opentelemetry-api` that provides a logs API at all."""


ROUTES = [
    Route(mode="span", on_spans=True, as_records=False),
    Route(mode="log", on_spans=False, as_records=True),
    Route(mode="both", on_spans=True, as_records=True),
    # The logs API is private, `opentelemetry-api` is depended on across a wide range, and
    # a release that moved it must cost the logs signal and nothing else. `events("log")`
    # and `events("both")` are refused on the builder instead; those two are in
    # tests/test_wire.py, because nothing gets as far as a span.
    Route(mode="span", on_spans=True, as_records=False, logs_api=False),
]

ROUTE_IDS = ["span", "log", "both", "span_without_the_logs_api"]


@pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS)
def test_the_events_mode_decides_which_signal_carries_each_event(
    httpserver: HTTPServer,
    runner: Runner,
    spans: Recorded,
    records: Logged,
    monkeypatch: pytest.MonkeyPatch,
    route: Route,
) -> None:
    if not route.logs_api:
        without_the_logs_api(monkeypatch)

    httpserver.expect_oneshot_request(EVALUATE, method="POST").respond_with_data(
        "", status=429, headers={"retry-after": "0"}
    )
    expect_post(httpserver).respond_with_data(noul_reply(0.95), content_type=JSON)

    assert runner.ask(noul("Urgent?"), TICKET, lambda builder: builder.events(route.mode)) is True

    ask = spans.one(ASK_SPAN)
    throttled = spans.named(ATTEMPT_SPAN)[0]
    assert ask.status.status_code is StatusCode.UNSET
    assert bool(spans.events(ask, ANSWER_EVENT)) is route.on_spans
    assert bool(spans.events(throttled, RETRY_EVENT)) is route.on_spans
    assert bool(records.named(ANSWER_EVENT)) is route.as_records
    assert bool(records.named(RETRY_EVENT)) is route.as_records


BOOM = "the log exporter is down"


@final
class Exploding:
    """An OpenTelemetry logger whose sink fails, the way a dead exporter does.

    The application owns the provider, the processor and the exporter; this stands in for
    all three, and everything between it and `Guide.ask` is the real code path.
    """

    def emit(self, record: object) -> None:  # noqa: ARG002 -- the sink never reads it
        """Fail on the call that hands the record over, which is where an exporter fails."""
        raise RuntimeError(BOOM)


def test_a_log_sink_that_fails_reaches_neither_the_caller_nor_the_ask_span(
    httpserver: HTTPServer, runner: Runner, spans: Recorded, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = telemetry.LOGS
    assert installed is not None
    # `Exploding` satisfies the `Logger` protocol for the one call guideme makes of it.
    sink = cast("Logger", Exploding())
    monkeypatch.setattr(telemetry, "LOGS", replace(installed, answers=sink, retries=sink))

    httpserver.expect_oneshot_request(EVALUATE, method="POST").respond_with_data(
        "", status=429, headers={"retry-after": "0"}
    )
    expect_post(httpserver).respond_with_data(noul_reply(0.95), content_type=JSON)

    assert runner.ask(noul("Urgent?"), TICKET) is True

    ask = spans.one(ASK_SPAN)
    assert ask.status.status_code is StatusCode.UNSET
    assert "error.type" not in attributes(ask)
    assert len(spans.events(ask, ANSWER_EVENT)) == 1
    assert len(spans.events(spans.named(ATTEMPT_SPAN)[0], RETRY_EVENT)) == 1
    assert not [text for text in spans.texts() if BOOM in text]
