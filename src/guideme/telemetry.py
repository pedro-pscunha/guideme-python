"""Spans, events and OTLP log records over `opentelemetry-api`. This module installs nothing.

No tracer provider, no logger provider, no exporter, no logging handler: until the
application installs a provider every call here goes to OpenTelemetry's no-op
implementation. What the names mean is `docs/observability.md`, and they are part of the
cross-SDK contract.

Two scopes, matching the two the Rust SDK filters on: `guideme` carries the ask span and
the answers, `guideme.api` the HTTP spans and the retries. An answer or a retry is written
to the span, to a log record, or to both, as `Events` says. A record emitted inside the
active span carries that span's trace and span ids, and that is what links the two signals.
"""

from contextlib import AbstractContextManager
from datetime import timedelta
from importlib.metadata import PackageNotFoundError, version
from typing import Literal

from opentelemetry._logs import Logger, LogRecord, SeverityNumber, get_logger
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

type Events = Literal["span", "log", "both"]
"""Where an answer or a retry is written: the span, an OTLP log record, or both.

`both` by default. With no logger provider installed the record goes to OpenTelemetry's
no-op logger, so `both` costs a traces-only application nothing and reproduces what the
Rust SDK emits unfiltered. An application running a traces pipeline and a logs pipeline
sets `span` or `log` to store each event once, which is what Rust's
`filter_fn(|meta| meta.is_span())` does there.
"""

EVENT_MODES: frozenset[str] = frozenset({"span", "log", "both"})
"""Every value `Events` allows, for the builder to refuse anything else by."""

_MILLISECOND = timedelta(milliseconds=1)


def _installed_version() -> str | None:
    """The distribution's version, or `None` where there is no distribution to read.

    A frozen bundle, a vendored copy, or an import straight off `src` has no installed
    metadata. An instrumentation scope without a version is valid, so that is what the
    tracers get; refusing to import would be the wrong trade.
    """
    try:
        return version("guideme")
    except PackageNotFoundError:
        return None


_VERSION = _installed_version()
_tracer = get_tracer("guideme", _VERSION)
_api_tracer = get_tracer("guideme.api", _VERSION)
# `get_logger` takes the version as a string, where `get_tracer` takes an optional one.
_logger = get_logger("guideme", _VERSION or "")
_api_logger = get_logger("guideme.api", _VERSION or "")


def _record(
    logger: Logger,
    name: str,
    severity: SeverityNumber,
    body: str,
    attributes: dict[str, Attribute],
) -> None:
    """Emit one OTLP log record, carrying the same attributes as the matching span event.

    Called from inside the span the event belongs to, which is where the record's trace and
    span ids come from: OpenTelemetry reads them off the active context when the record is
    built. The text is the severity's own name, so the two cannot drift. `INFO` for an
    answer and `WARN` for a retry are the only severities guideme uses; nothing is ever
    emitted at `ERROR`.
    """
    logger.emit(
        LogRecord(
            event_name=name,
            severity_text=severity.name,
            severity_number=severity,
            body=body,
            attributes=attributes,
        )
    )


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


def answer_event(
    span: Span, qid: str, outcome: Outcome, thresholds: Thresholds, events: Events
) -> None:
    """One `guideme.answer`, with the thresholds the verdict came from.

    Emitted from the resolved outcome, before the unsure ladder picks a fallback, so it
    says what the model answered rather than what the caller ended up with. The span event
    and the log record carry the same attributes; the record adds the message the Rust SDK
    writes, at `INFO`.
    """
    match outcome:
        case NoulOutcome(verdict=verdict):
            judged: dict[str, Attribute] = {
                "guideme.kind": "noul",
                "guideme.outcome": verdict.verdict,
                "guideme.probability": verdict.p,
                "guideme.unsure": verdict.verdict == "unsure",
            }
            body = f"{qid} noul: {verdict.verdict}"
        case ChoiceOutcome(key=key, confidence=reported, unsure=unsure):
            judged = {
                "guideme.kind": "choice",
                "guideme.outcome": key,
                "guideme.confidence": reported,
                "guideme.unsure": unsure,
            }
            body = f"{qid} choice: {key}"
        case ScoreOutcome(index=index, value=value, confidence=reported, unsure=unsure):
            judged = {
                "guideme.kind": "score",
                "guideme.outcome": index,
                "guideme.value": value,
                "guideme.confidence": reported,
                "guideme.unsure": unsure,
            }
            body = f"{qid} score: level {index}"
    attributes: dict[str, Attribute] = {
        "guideme.question": qid,
        **judged,
        "guideme.yes_above": thresholds.yes_above,
        "guideme.no_below": thresholds.no_below,
        "guideme.min_confidence": thresholds.min_confidence,
    }
    if events != "log":
        span.add_event(ANSWER_EVENT, attributes)
    if events != "span":
        _record(_logger, ANSWER_EVENT, SeverityNumber.INFO, body, attributes)


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


def retry_event(span: Span, status: int, attempt: int, delay: timedelta, events: Events) -> None:
    """One `guideme.retry`. `attempt` is the ordinal of the resend about to be made.

    The log record is the `WARN` the Rust SDK logs; a span event carries no severity, so
    on the span its presence is the signal.
    """
    delay_ms = delay // _MILLISECOND
    attributes: dict[str, Attribute] = {
        "http.response.status_code": status,
        "guideme.retry.attempt": attempt,
        "guideme.retry.delay_ms": delay_ms,
    }
    if events != "log":
        span.add_event(RETRY_EVENT, attributes)
    if events != "span":
        body = f"{status} from TypeSafe, retrying in {delay_ms} ms"
        _record(_api_logger, RETRY_EVENT, SeverityNumber.WARN, body, attributes)
