"""guideme against the live TypeSafe API, exporting spans and log records over OTLP.

`README.md` next to this file has the collector recipe and how to run this. Both
exporters are configured entirely by the standard OTLP environment variables, so
pointing them at a vendor instead of the local collector is a matter of setting
those. With no collector listening the program still runs and still prints its
answers; the exporters report the failure when they are shut down.
"""

import os
from contextlib import closing

from opentelemetry import trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from guideme import (
    ApiKey,
    Choice,
    ConfigError,
    Guide,
    Levels,
    Policy,
    UnsureError,
    choose,
    fallback,
    noul,
    score,
)

TICKET = (
    "Help! My payouts have been failing for 3 days and nobody answers. "
    "I was charged twice and I want the second charge back."
)


class Department(Choice):
    """Where a support ticket should be routed. A member's value is its rubric."""

    billing = "Payments, invoicing, refunds"
    technical = "Bugs, outages, integrations"
    sales = fallback("Pricing, upgrades, new accounts")


class Frustration(Levels):
    """How the customer sounds, low to high. Declaration order is level order."""

    calm = "Calm and polite"
    frustrated = "Frustrated"
    very_angry = "Very angry"


def telemetry() -> tuple[TracerProvider, LoggerProvider]:
    """Install providers that batch spans and log records to an OTLP collector over gRPC."""
    # Resource.create() reads OTEL_SERVICE_NAME and OTEL_RESOURCE_ATTRIBUTES itself, and an
    # attribute passed here beats them, so the default applies only when nothing is set.
    named = os.environ.get("OTEL_SERVICE_NAME")
    resource = Resource.create() if named else Resource.create({SERVICE_NAME: "support-triage"})
    traces = TracerProvider(resource=resource)
    traces.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(traces)
    logs = LoggerProvider(resource=resource)
    logs.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    set_logger_provider(logs)
    return traces, logs


def configured() -> Guide:
    """A guide from the environment that writes each event once, as a log record.

    `Guide.from_env()` would cover the key on its own, but the signal is a builder
    setting and both pipelines here point at the same collector, so `events("log")` is
    what keeps an answer from being stored twice. It is this example's counterpart to the
    Rust example's `filter_fn(|meta| meta.is_span())`.
    """
    key = os.environ.get("TYPESAFE_API_KEY")
    if key is None:
        detail = "TYPESAFE_API_KEY is not set"
        raise ConfigError(detail)
    return Guide.builder().api_key(ApiKey(key)).events("log").build()


def run(guide: Guide) -> None:
    """Ask the support-triage questions and print every answer."""
    # One question: one ask span, one answer record.
    urgent = guide.ask(noul("Does this convey urgency?"), TICKET)
    print(f"urgent: {urgent}")

    # Three questions in one request: one ask span, three answer records.
    dept, mood, refund = guide.ask(
        (
            choose(Department, "Which team should handle this?").min_confidence(0.6),
            score(Frustration, "How frustrated is the customer?").detail(),
            noul("Is the customer asking for a refund?")
            .yes_above(0.8)
            .no_below(0.2)
            .otherwise(False),
        ),
        TICKET,
    )
    print(f"department: {dept.name}")
    print(f"frustration: {mood.level.name} at {mood.value:.2f}")
    print(f"refund requested: {refund}")

    # A floor nothing can satisfy. A score has no fallback member, so this one fails and its
    # span carries error.type = "unsure" with an error status.
    strict = guide.with_policy(Policy(min_confidence=0.999))
    try:
        level = strict.ask(score(Frustration, "How frustrated is the customer?"), TICKET)
    except UnsureError as error:
        print(f"strict frustration failed as expected: {error}")
    else:
        print(f"strict frustration: {level.name}")


def main() -> None:
    """Install the exporters, ask the questions, and flush whatever happened."""
    traces, logs = telemetry()
    try:
        with closing(configured()) as guide:
            run(guide)
    finally:
        traces.shutdown()
        logs.shutdown()


if __name__ == "__main__":
    main()
