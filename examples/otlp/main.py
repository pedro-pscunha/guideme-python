"""guideme against the live TypeSafe API, exporting every span over OTLP.

`README.md` next to this file has the collector recipe and how to run this. The
exporter is configured entirely by the standard OTLP environment variables, so
pointing it at a vendor instead of the local collector is a matter of setting
those. With no collector listening the program still runs and still prints its
answers; the exporter reports the failure when it is shut down.
"""

import os
from contextlib import closing

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from guideme import Choice, Guide, Levels, Policy, UnsureError, choose, fallback, noul, score

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


def telemetry() -> TracerProvider:
    """Install a provider that batches spans to an OTLP collector over gRPC."""
    # Resource.create() reads OTEL_SERVICE_NAME and OTEL_RESOURCE_ATTRIBUTES itself, and an
    # attribute passed here beats them, so the default applies only when nothing is set.
    named = os.environ.get("OTEL_SERVICE_NAME")
    resource = Resource.create() if named else Resource.create({SERVICE_NAME: "support-triage"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    return provider


def run(guide: Guide) -> None:
    """Ask the support-triage questions and print every answer."""
    # One question: one ask span, one answer event.
    urgent = guide.ask(noul("Does this convey urgency?"), TICKET)
    print(f"urgent: {urgent}")

    # Three questions in one request: one ask span, three answer events.
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
    """Install the exporter, ask the questions, and flush whatever happened."""
    provider = telemetry()
    try:
        with closing(Guide.from_env()) as guide:
            run(guide)
    finally:
        provider.shutdown()


if __name__ == "__main__":
    main()
