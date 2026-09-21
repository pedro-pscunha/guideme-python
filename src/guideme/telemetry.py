"""Spans and events over `opentelemetry-api`. This module installs nothing.

No tracer provider, no exporter, no logging handler: until the application installs a
provider every call here goes to OpenTelemetry's no-op implementation. What the names
mean is `docs/observability.md`, and they are part of the cross-SDK contract.

Two scopes, matching the two the Rust SDK filters on: `guideme` carries the ask span and
the answer events, `guideme.api` the HTTP spans and the retry events.
"""

from contextlib import AbstractContextManager
from datetime import timedelta
from importlib.metadata import version

from opentelemetry.trace import Span, SpanKind, StatusCode, get_tracer

from guideme.errors import GuidemeError
from guideme.policy import ChoiceOutcome, NoulOutcome, Outcome, ScoreOutcome, Thresholds

type Attribute = str | int | float | bool
"""What an OpenTelemetry attribute may hold here. Counts are `int`, never a string."""

PROVIDER = "typesafe"
"""`gen_ai.provider.name`. One value; guideme talks to one API."""

OPERATION = "ask"
"""`gen_ai.operation.name`.

Deliberately not a well-known value such as `chat`: a Jev judgment sends structured
questions and gets probabilities back, and a product keying on `chat` would read it
as a conversation.
"""

ASK_SPAN = "guideme.ask"
"""One per `ask`, whatever the shape. A fixed name is what a dashboard keys on."""

ANSWER_EVENT = "guideme.answer"
"""One per question answered."""

RETRY_EVENT = "guideme.retry"
"""One per throttled attempt, just before the wait. The warning of the Rust SDK."""

_MILLISECOND = timedelta(milliseconds=1)
_VERSION = version("guideme")
_tracer = get_tracer("guideme", _VERSION)
_api_tracer = get_tracer("guideme.api", _VERSION)


def ask_span(
    model: str, host: str, port: int, questions: int, state_bytes: int, state: str | None
) -> AbstractContextManager[Span]:
    """Open the `guideme.ask` span. `state` is present only when `record_state(True)` is set.

    guideme marks its own failures, so the span neither records an exception event nor
    lets one set its status; `fail_ask` does both explicitly.
    """
    attributes: dict[str, Attribute] = {
        "gen_ai.provider.name": PROVIDER,
        "gen_ai.operation.name": OPERATION,
        "gen_ai.request.model": model,
        "server.address": host,
        "server.port": port,
        "guideme.questions": questions,
        "guideme.state.bytes": state_bytes,
    }
    if state is not None:
        attributes["guideme.state"] = state
    return _tracer.start_as_current_span(
        ASK_SPAN,
        kind=SpanKind.CLIENT,
        attributes=attributes,
        record_exception=False,
        set_status_on_exception=False,
    )


def record_response(span: Span, model: str, input_tokens: int, output_tokens: int) -> None:
    """Record what the response reported. Absent on a span that failed in transport."""
    span.set_attribute("gen_ai.response.model", model)
    span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", output_tokens)


def answer_event(span: Span, qid: str, outcome: Outcome, thresholds: Thresholds) -> None:
    """One `guideme.answer` event, with the thresholds the verdict came from.

    Emitted from the resolved outcome, before the unsure ladder picks a fallback, so it
    says what the model answered rather than what the caller ended up with.
    """
    match outcome:
        case NoulOutcome(verdict=verdict):
            judged: dict[str, Attribute] = {
                "guideme.kind": "noul",
                "guideme.outcome": verdict.verdict,
                "guideme.probability": verdict.p,
                "guideme.unsure": verdict.verdict == "unsure",
            }
        case ChoiceOutcome(key=key, confidence=reported, unsure=unsure):
            judged = {
                "guideme.kind": "choice",
                "guideme.outcome": key,
                "guideme.confidence": reported,
                "guideme.unsure": unsure,
            }
        case ScoreOutcome(index=index, value=value, confidence=reported, unsure=unsure):
            judged = {
                "guideme.kind": "score",
                "guideme.outcome": index,
                "guideme.value": value,
                "guideme.confidence": reported,
                "guideme.unsure": unsure,
            }
    span.add_event(
        ANSWER_EVENT,
        {
            "guideme.question": qid,
            **judged,
            "guideme.yes_above": thresholds.yes_above,
            "guideme.no_below": thresholds.no_below,
            "guideme.min_confidence": thresholds.min_confidence,
        },
    )


def fail_ask(span: Span, error: GuidemeError) -> None:
    """Mark the ask span failed: the stable `error.type`, and the status with a description.

    No exception event is recorded. A failure is returned as a typed error and marked
    here; whether it is logged is the application's decision.
    """
    span.set_attribute("error.type", error.kind)
    span.set_status(StatusCode.ERROR, error.describe())


def attempt_span(
    method: str, url: str, template: str, host: str, port: int, attempt: int
) -> AbstractContextManager[Span]:
    """Open one HTTP attempt's span. `attempt` is `0` for the first send.

    `url` never carries a secret: `Endpoint.parse` refuses a base URL with credentials.
    """
    attributes: dict[str, Attribute] = {
        "http.request.method": method,
        "server.address": host,
        "server.port": port,
        "url.full": url,
        "url.template": template,
    }
    if attempt > 0:
        attributes["http.request.resend_count"] = attempt
    return _api_tracer.start_as_current_span(
        f"{method} {template}",
        kind=SpanKind.CLIENT,
        attributes=attributes,
        record_exception=False,
        set_status_on_exception=False,
    )


def record_status(span: Span, status: int) -> None:
    """Record the status a response arrived with. Absent when none did."""
    span.set_attribute("http.response.status_code", status)


def fail_attempt(span: Span, error_type: str) -> None:
    """Mark one attempt failed.

    `error_type` is the status as text when a response arrived, otherwise the error's
    kind. guideme's contract makes `200` the only success, so every other status marks
    the attempt even where the HTTP conventions would leave it unset.
    """
    span.set_attribute("error.type", error_type)
    span.set_status(StatusCode.ERROR)


def retry_event(span: Span, status: int, attempt: int, delay: timedelta) -> None:
    """One `guideme.retry` event. `attempt` is the ordinal of the resend about to be made."""
    span.add_event(
        RETRY_EVENT,
        {
            "http.response.status_code": status,
            "guideme.retry.attempt": attempt,
            "guideme.retry.delay_ms": delay // _MILLISECOND,
        },
    )
