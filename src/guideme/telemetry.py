"""Spans, events and OTLP log records over `opentelemetry-api`. This module installs nothing.

No tracer provider, no logger provider, no exporter, no logging handler: until the
application installs a provider every call here goes to OpenTelemetry's no-op
implementation. What the names mean is `docs/observability.md`, and they are part of the
cross-SDK contract.

Two scopes, matching the two the Rust SDK filters on: `guideme` carries the ask span and
the answers, `guideme.api` the HTTP spans and the retries. An answer or a retry is written
to the span, to a log record, or to both, as `Events` says. A record emitted inside the
active span carries that span's trace and span ids, and that is what links the two signals.

The traces API is a hard requirement; the logs API is not. It is private in
`opentelemetry-api` and this package accepts a wide range of that package, so it is
resolved defensively and its absence is a value, `LOGS`. Without it the traces signal is
untouched and asking for records is refused where the guide is configured.
"""

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import timedelta
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Literal, final

from opentelemetry.trace import Span, SpanKind, StatusCode, get_tracer

from guideme.errors import GuidemeError
from guideme.policy import ChoiceOutcome, NoulOutcome, Outcome, ScoreOutcome, Thresholds

if TYPE_CHECKING:
    from opentelemetry._logs import Logger, LogRecord, SeverityNumber

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


LOGS_MODULE = "opentelemetry._logs"
"""Where `opentelemetry-api` keeps the logs API. The underscore is OpenTelemetry's own."""

OTEL_API = "opentelemetry-api"
"""The distribution the logs API would come from, named in the refusal when it has none."""


def _installed_version(package: str) -> str | None:
    """A distribution's version, or `None` where there is no distribution to read.

    A frozen bundle, a vendored copy, or an import straight off `src` has no installed
    metadata. An instrumentation scope without a version is valid, so that is what the
    tracers get; refusing to import would be the wrong trade.
    """
    try:
        return version(package)
    except PackageNotFoundError:
        return None


_VERSION = _installed_version("guideme")
_tracer = get_tracer("guideme", _VERSION)
_api_tracer = get_tracer("guideme.api", _VERSION)


@final
@dataclass(frozen=True, slots=True)
class Logs:
    """The logs signal: the two loggers, the record type, and the two severities used.

    Held as one value resolved at import so that everything the private logs API supplies
    is reached through a single object whose absence is representable.
    """

    answers: "Logger"
    retries: "Logger"
    build: "type[LogRecord]"
    info: "SeverityNumber"
    warn: "SeverityNumber"

    def answer(self, body: str, attributes: dict[str, Attribute]) -> None:
        """One answer as a log record, at `INFO`, in the `guideme` scope."""
        self._emit(self.answers, ANSWER_EVENT, self.info, body, attributes)

    def retry(self, body: str, attributes: dict[str, Attribute]) -> None:
        """One retry as a log record, at `WARN`, in the `guideme.api` scope."""
        self._emit(self.retries, RETRY_EVENT, self.warn, body, attributes)

    def _emit(
        self,
        logger: "Logger",
        name: str,
        severity: "SeverityNumber",
        body: str,
        attributes: dict[str, Attribute],
    ) -> None:
        """Emit one OTLP log record, carrying the same attributes as the matching span event.

        Called from inside the span the event belongs to, which is where the record's trace
        and span ids come from: OpenTelemetry reads them off the active context when the
        record is built. The text is the severity's own name, so the two cannot drift.
        `INFO` for an answer and `WARN` for a retry are the only severities guideme uses;
        nothing is ever emitted at `ERROR`.

        A sink that raises is swallowed, and that is the one deliberate exception to this
        package's fail-loudly rule, recorded as such in `AGENTS.md`. The provider, the
        processor and the exporter are the application's; an ask that reached its answer
        must still return it, and a span that says the ask succeeded must not be
        contradicted by a logging failure. The failure belongs to the application's own
        logging pipeline, which is where it is visible.

        The record is built outside that guard so the swallow covers only what the
        docstring scopes it to. A `LogRecord` this package cannot construct is not a
        sink failure, it is the private API having moved, and it is caught once in
        `resolve_logs` where absence is representable and says so loudly.
        """
        record = self.build(
            event_name=name,
            severity_text=severity.name,
            severity_number=severity,
            body=body,
            attributes=attributes,
        )
        try:  # noqa: SIM105 -- an explicit `except Exception` is what AGENTS.md audits for
            logger.emit(record)
        except Exception:  # noqa: BLE001, S110 -- a sink's failure is not the ask's  # pylint: disable=broad-exception-caught
            pass


def resolve_logs() -> Logs | None:
    """The logs signal, or `None` where this `opentelemetry-api` does not provide one.

    The logs API is private. `opentelemetry-api` keeps it at `opentelemetry._logs` with no
    public alias, and this package depends on a range wide enough for a future release to
    move it. Imported at module scope, such a release would break `import guideme` outright
    for every user, traces included; imported here, it costs the logs signal alone.

    Everything this package needs from that API is reached inside the one guard, because
    the promise is about the signal and not about the import: a release that keeps the
    module and moves what is in it must cost no more than a release that deletes it.
    Three failures are expected and each is the same fact, that this `opentelemetry-api`
    provides no logs API guideme can use. `ImportError` is the module or a name being
    gone; `AttributeError` is a severity member renamed; `TypeError` is `get_logger` or
    `LogRecord` taking different arguments. Anything else that package raises is still a
    failure and still propagates.
    """
    # pylint: disable=import-outside-toplevel; the one import here is deferred on purpose
    try:
        from opentelemetry._logs import (  # noqa: PLC0415 -- deferred; see the docstring
            LogRecord,
            SeverityNumber,
            get_logger,
        )

        info = SeverityNumber.INFO
        warn = SeverityNumber.WARN
        # `get_logger` takes the version as a string, where `get_tracer` takes an optional one.
        answers = get_logger("guideme", _VERSION or "")
        retries = get_logger("guideme.api", _VERSION or "")
        # One throwaway record, built with exactly the five keywords `Logs._emit` uses, so a
        # constructor that no longer takes them is found here rather than at the first emit.
        # `_emit` swallows a sink that raises; it must never be what hides this.
        _ = LogRecord(
            event_name=ANSWER_EVENT,
            severity_text=info.name,
            severity_number=info,
            body="",
            attributes={},
        )
    except (ImportError, AttributeError, TypeError):
        return None
    return Logs(answers=answers, retries=retries, build=LogRecord, info=info, warn=warn)


LOGS = resolve_logs()
"""The logs signal for this process, resolved once.

`None` means this `opentelemetry-api` has no logs API. `GuideBuilder.events` then refuses
`"log"` and `"both"`, so a caller who asked for records is told why rather than quietly
getting none. The default `"both"` is not such an ask: it is what a guide carries when
nobody chose, so it degrades to the span alone, which is also all an application without
the logs API could have exported.
"""


def logs_available() -> bool:
    """Whether this `opentelemetry-api` provides the logs API a log record needs."""
    return LOGS is not None


def logs_missing(where: str) -> str:
    """Why `events(where)` cannot be honoured, naming the version and both ways out."""
    found = _installed_version(OTEL_API)
    named = f"{OTEL_API} {found}" if found else f"the installed {OTEL_API}"
    return (
        f'events("{where}") needs the OpenTelemetry logs API, and {named} has none at '
        f"{LOGS_MODULE}. Pin {OTEL_API} to a release that provides it, or use "
        f'events("span") to keep answers and retries on the ask span.'
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
    logs = LOGS
    if events != "span" and logs is not None:
        logs.answer(body, attributes)


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
    logs = LOGS
    if events != "span" and logs is not None:
        logs.retry(f"{status} from TypeSafe, retrying in {delay_ms} ms", attributes)
