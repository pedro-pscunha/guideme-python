# pylint: disable=redefined-outer-name  # a pytest fixture is requested by its own name
import asyncio
import dataclasses
import json
import socket
import sys
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast, final

import pytest
from hypothesis import HealthCheck, settings
from jsonschema import Draft202012Validator
from opentelemetry import trace
from opentelemetry._logs import LogRecord, set_logger_provider
from opentelemetry.sdk._logs import LoggerProvider, ReadableLogRecord
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor
from opentelemetry.sdk.trace import Event, ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import TypeAdapter
from pytest_httpserver import HTTPServer
from pytest_httpserver.httpserver import RequestHandler

from guideme import ApiKey, AsyncGuide, Guide, GuideBuilder, telemetry
from guideme._json import Json
from guideme.api import Answer as WireAnswer
from guideme.api import answer_from_wire
from guideme.api.client import EVALUATE
from guideme.guide import ModelInfo
from guideme.policy import (
    Answer,
    ChoiceOutcome,
    NoulOutcome,
    Outcome,
    ScoreOutcome,
)
from guideme.question import choose_among, noul, score_levels

if TYPE_CHECKING:
    from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]

settings.register_profile(
    "ci",
    max_examples=200,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("ci")


def narrow(value: object) -> Json:
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, list):
        # json.loads builds only list/dict/str/int/float/bool/None, so every
        # element is one of those and this same function narrows it.
        return [narrow(item) for item in cast("list[object]", value)]
    if isinstance(value, dict):
        # Same, and json.loads only ever produces str keys.
        return {k: narrow(v) for k, v in cast("dict[str, object]", value).items()}
    message = f"not JSON: {type(value).__name__}"
    raise TypeError(message)


def load_json(path: Path) -> Json:
    parsed: object = json.loads(path.read_text(encoding="utf-8"))
    return narrow(parsed)


def as_list(value: Json) -> list[Json]:
    assert isinstance(value, list)
    return value


def as_object(value: Json) -> dict[str, Json]:
    assert isinstance(value, dict)
    return value


def as_str(value: Json) -> str:
    assert isinstance(value, str)
    return value


def as_float(value: Json) -> float:
    assert not isinstance(value, bool)
    assert isinstance(value, int | float)
    return float(value)


WIRE_ANSWER: TypeAdapter[WireAnswer] = TypeAdapter(WireAnswer)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCHEMAS = REPO_ROOT / "spec" / "schema"


def answer_from_json(raw: Json) -> Answer:
    """A golden vector's answer through the real wire layer, exactly as an ask does it."""
    return answer_from_wire(WIRE_ANSWER.validate_python(raw))


def validator(name: str) -> Callable[[object], None]:
    """A checker against one vendored schema.

    `jsonschema` ships no `py.typed`, so its surface is pulled in here once, behind a
    declared signature, instead of spreading inferred types through the tests.
    """
    checker = Draft202012Validator(as_object(load_json(SCHEMAS / f"{name}.json")))

    def check(instance: object) -> None:
        checker.validate(instance)  # pyright: ignore[reportUnknownMemberType] -- no py.typed

    return check


def _outcome_json(outcome: Outcome) -> Json:
    match outcome:
        case NoulOutcome(verdict=verdict):
            return {"kind": "noul", "verdict": verdict.verdict, "p": verdict.p}
        case ChoiceOutcome(key=key, confidence=c, unsure=unsure, ranked=ranked):
            return {
                "kind": "choice",
                "key": key,
                "confidence": c,
                "unsure": unsure,
                "ranked": [[k, p] for k, p in ranked],
            }
        case ScoreOutcome(
            index=index,
            value=value,
            confidence=c,
            unsure=unsure,
            distribution=distribution,
            legend=legend,
        ):
            return {
                "kind": "score",
                "index": index,
                "value": value,
                "confidence": c,
                "unsure": unsure,
                "distribution": list(distribution),
                "legend": list(legend),
            }


def _carried_fields(outcome: Outcome) -> set[str]:
    """Every field the JSON form must carry besides `kind`.

    A `NoulOutcome` holds one nested `Verdict` and the contract flattens it, so its
    two fields are what the JSON must show; the other two outcomes are flat already.
    """
    match outcome:
        case NoulOutcome(verdict=verdict):
            return {field.name for field in dataclasses.fields(verdict)}
        case ChoiceOutcome() | ScoreOutcome():
            return {field.name for field in dataclasses.fields(outcome)}


def outcome_json(outcome: Outcome) -> Json:
    """One outcome as the contract's JSON, proven to carry every field the dataclass has."""
    produced = _outcome_json(outcome)
    assert set(as_object(produced)) == _carried_fields(outcome) | {"kind"}
    return produced


TEST_KEY = "unit-test-key-not-a-secret"
"""What the local server is told to expect. Nothing real; the live tests read the real key."""

MODEL = "jev-1.13.0"
"""The versioned model id the docs fixtures report and every local reply echoes."""

JSON = "application/json"
"""The one content type the API sends and receives."""

TICKET = "Help! My payouts have been failing for 3 days."
"""The state every wire test asks about."""

BATCH_ANSWERS: dict[str, Json] = {
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
"""One answer per question of `batch()`, one of each kind."""


def batch() -> tuple[object, object, object]:
    """The three-question batch `BATCH_ANSWERS` answers: one noul, one choice, one score."""
    return (
        noul("Urgent?").criteria("needs a person now", "can wait"),
        choose_among("Which team?", {"billing": "Money", "sales": None}),
        score_levels("How cross?", ["Calm", "Cross"]),
    )


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
    """A one-question reply whose `q0` is a noul at `probability`."""
    return reply({"q0": {"type": "noul", "noul": probability}})


def expect_post(httpserver: HTTPServer) -> RequestHandler:
    """The handler every `POST /v1/systemone` of one test goes to."""
    return httpserver.expect_request(EVALUATE, method="POST")


type Kind = Literal["sync", "async"]
"""Which executor a `Runner` drives. Every wire and tracing assertion runs as both."""

SYNC: Kind = "sync"
ASYNC: Kind = "async"

type Configure = Callable[[GuideBuilder], GuideBuilder]
"""A test's extra builder settings, applied after the ones every test shares."""


def kind_of(param: object) -> Kind:
    """Narrow a fixture parameter to a kind. Anything else is a failure, never a default."""
    if param == SYNC:
        return SYNC
    if param == ASYNC:
        return ASYNC
    message = f"unknown runner kind: {param!r}"
    raise ValueError(message)


def closed_port() -> int:
    """A port nothing listens on: bound only to be told a free one, then released."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        # An AF_INET socket's name is always (host, port); the bind above fixes that.
        return cast("tuple[str, int]", probe.getsockname())[1]


def nowhere(builder: GuideBuilder) -> GuideBuilder:
    """Point a guide at a closed port, so the next ask fails in transport."""
    return builder.base_url(f"http://127.0.0.1:{closed_port()}").max_retries(0)


def client_vars(guide: Guide | AsyncGuide) -> str:
    """`vars` of a guide's client, where a bearer held in instance state would show.

    Reaching for the private attribute is the point: what this proves is that nothing
    inside the client holds the key, and that is a statement about its private state.
    """
    # pylint: disable=protected-access  # the redaction proof reads what is private on purpose
    inner = guide._client  # noqa: SLF001 # pyright: ignore[reportPrivateUsage] -- redaction proof
    return repr(vars(inner))


def configured(base_url: str, configure: Configure | None = None) -> GuideBuilder:
    """The builder every local-server test starts from: the test key, that origin, 10 ms backoff."""
    builder = (
        GuideBuilder()
        .api_key(ApiKey(TEST_KEY))
        .base_url(base_url)
        .backoff(timedelta(milliseconds=10))
    )
    return builder if configure is None else configure(builder)


def _entry(guide: Guide) -> Callable[[object, Json], object]:
    """`Guide.ask`, widened to the signature its implementation actually has.

    `ask` is a stack of overloads over concrete shapes, and this fixture is
    shape-agnostic on purpose, so it cannot name one. The call still goes through the
    public method; `tests/typing/` is what proves the overloads carry the right types.
    """
    return cast("Callable[[object, Json], object]", guide.ask)


def async_entry(guide: AsyncGuide) -> Callable[[object, Json], Awaitable[object]]:
    """`AsyncGuide.ask`, widened for the same reason as `_entry`."""
    return cast("Callable[[object, Json], Awaitable[object]]", guide.ask)


@final
@dataclass(frozen=True, slots=True)
class Runner:
    """One ask against the local server, run once as `Guide` and once as `AsyncGuide`.

    Both kinds build, ask and close inside the same call, so an `AsyncGuide`'s pool is
    never left open across event loops.
    """

    kind: Kind
    base_url: str

    def _builder(self, configure: Configure | None) -> GuideBuilder:
        return configured(self.base_url, configure)

    def ask(self, shape: object, state: Json, configure: Configure | None = None) -> object:
        """Ask `shape` about `state` and return what the caller's shape reads back as."""
        if self.kind == SYNC:
            guide = self._builder(configure).build()
            try:
                return _entry(guide)(shape, state)
            finally:
                guide.close()
        return asyncio.run(self._ask_async(shape, state, configure))

    async def _ask_async(self, shape: object, state: Json, configure: Configure | None) -> object:
        guide = self._builder(configure).build_async()
        try:
            return await async_entry(guide)(shape, state)
        finally:
            await guide.close()

    def models(self) -> tuple[ModelInfo, ...]:
        """`GET /v1/models` through this kind's executor."""
        if self.kind == SYNC:
            guide = self._builder(None).build()
            try:
                return guide.models()
            finally:
                guide.close()
        return asyncio.run(self._models_async())

    async def _models_async(self) -> tuple[ModelInfo, ...]:
        guide = self._builder(None).build_async()
        try:
            return await guide.models()
        finally:
            await guide.close()

    def internals(self) -> str:
        """`vars` of this kind's client, built and closed the way every other call is."""
        if self.kind == SYNC:
            guide = self._builder(None).build()
            try:
                return client_vars(guide)
            finally:
                guide.close()
        return asyncio.run(self._internals_async())

    async def _internals_async(self) -> str:
        guide = self._builder(None).build_async()
        try:
            return client_vars(guide)
        finally:
            await guide.close()

    def described(self, configure: Configure | None = None) -> str:
        """`repr` of a guide of this kind, without asking anything."""
        if self.kind == SYNC:
            guide = self._builder(configure).build()
            try:
                return repr(guide)
            finally:
                guide.close()
        return asyncio.run(self._described_async(configure))

    async def _described_async(self, configure: Configure | None) -> str:
        guide = self._builder(configure).build_async()
        try:
            return repr(guide)
        finally:
            await guide.close()


@pytest.fixture(params=[SYNC, ASYNC])
def runner(request: pytest.FixtureRequest, httpserver: HTTPServer) -> Runner:
    """Every wire and tracing assertion runs twice, once per kind, from this one fixture."""
    return Runner(kind=kind_of(request.param), base_url=httpserver.url_for(""))


def without_the_logs_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make guideme see an `opentelemetry-api` that provides no logs API, for one test.

    `None` in `sys.modules` is how CPython marks a module unimportable: the import
    machinery raises `ImportError` on it. `resolve_logs` therefore takes the very branch a
    release that moved `opentelemetry._logs` would put it on, so what these tests exercise
    is the real resolution and not a stand-in for it.
    """
    # `sys.modules` is annotated `dict[str, ModuleType]`, and the `None` sentinel is older
    # than that annotation; it is the documented way to block an import.
    monkeypatch.setitem(sys.modules, telemetry.LOGS_MODULE, cast("ModuleType", None))
    monkeypatch.setattr(telemetry, "LOGS", telemetry.resolve_logs())
    assert telemetry.LOGS is None


@final
class DriftedLogRecord(LogRecord):
    """A `LogRecord` from a release that stopped taking `event_name`.

    The module still imports and every name is still there; only the constructor moved.
    That is the shape of drift `ImportError` cannot see.
    """

    def __init__(self, **fields: object) -> None:
        if "event_name" in fields:
            message = "LogRecord.__init__() got an unexpected keyword argument 'event_name'"
            raise TypeError(message)
        super().__init__()


def with_a_drifted_log_record(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make guideme see an `opentelemetry-api` whose `LogRecord` it cannot build, for one test.

    `resolve_logs` imports the name off the module every call, so replacing the attribute
    is what a release moving the constructor would look like from inside this package. The
    signal has to go absent here: a record guideme cannot construct reaches no exporter,
    and `Logs._emit` must not be the thing that discovers it.
    """
    monkeypatch.setattr(f"{telemetry.LOGS_MODULE}.LogRecord", DriftedLogRecord)
    monkeypatch.setattr(telemetry, "LOGS", telemetry.resolve_logs())
    assert telemetry.LOGS is None


@pytest.fixture
def other_httpserver() -> Iterator[HTTPServer]:
    """A second local server, on its own port, so a redirect can leave the first origin."""
    server = HTTPServer(host="127.0.0.1", port=0)
    server.start()
    try:
        yield server
    finally:
        server.clear()
        server.stop()


@final
@dataclass(frozen=True, slots=True)
class Recorded:
    """What one test exported, queried by name."""

    exporter: InMemorySpanExporter

    def all(self) -> tuple[ReadableSpan, ...]:
        """Every span that finished during this test, in the order they finished."""
        return self.exporter.get_finished_spans()

    def named(self, name: str) -> list[ReadableSpan]:
        """Every span with this name."""
        return [span for span in self.all() if span.name == name]

    def one(self, name: str) -> ReadableSpan:
        """The single span with this name, or a failure naming what was there instead."""
        found = self.named(name)
        assert len(found) == 1, [span.name for span in self.all()]
        return found[0]

    def events(self, span: ReadableSpan, name: str) -> list[Event]:
        """Every event on `span` with this name."""
        return [event for event in span.events if event.name == name]

    def reset(self) -> None:
        """Drop everything exported so far, so what a test does next is read on its own."""
        self.exporter.clear()

    def texts(self) -> list[str]:
        """Every attribute value and span status description exported, as text.

        What a redaction proof walks: a secret or a caller's state is absent only if it
        is in none of these.
        """
        return [
            str(value)
            for span in self.all()
            for carrier in (span, *span.events)
            for value in attributes(carrier).values()
        ] + [span.status.description for span in self.all() if span.status.description]


def attributes(carrier: ReadableSpan | Event) -> dict[str, object]:
    """A span's or an event's attributes as a plain dict."""
    return dict(carrier.attributes or {})


def log_attributes(record: ReadableLogRecord) -> dict[str, object]:
    """One log record's attributes as a plain dict, comparable with a span event's."""
    return dict(record.log_record.attributes or {})


def log_scope(record: ReadableLogRecord) -> str:
    """The instrumentation scope one record came from: `guideme` or `guideme.api`."""
    scope = record.instrumentation_scope
    assert scope is not None
    return scope.name


@final
@dataclass(frozen=True, slots=True)
class Logged:
    """What one test emitted on the logs signal, queried by event name."""

    exporter: InMemoryLogRecordExporter

    def all(self) -> tuple[ReadableLogRecord, ...]:
        """Every record emitted during this test, in the order they were emitted."""
        return self.exporter.get_finished_logs()

    def named(self, event_name: str) -> list[ReadableLogRecord]:
        """Every record carrying this event name."""
        return [record for record in self.all() if record.log_record.event_name == event_name]

    def one(self, event_name: str) -> ReadableLogRecord:
        """The single record with this event name, or a failure naming what was there instead."""
        found = self.named(event_name)
        assert len(found) == 1, [record.log_record.event_name for record in self.all()]
        return found[0]


@pytest.fixture(scope="session")
def exporter() -> InMemorySpanExporter:
    """The one tracer provider this process installs. OpenTelemetry allows exactly one."""
    collected = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(collected))
    trace.set_tracer_provider(provider)
    return collected


@pytest.fixture
def spans(exporter: InMemorySpanExporter) -> Iterator[Recorded]:
    """What one test exported, with whatever earlier tests left behind cleared away."""
    exporter.clear()
    yield Recorded(exporter)
    exporter.clear()


@pytest.fixture(scope="session")
def log_exporter() -> InMemoryLogRecordExporter:
    """The one logger provider this process installs. OpenTelemetry allows exactly one.

    guideme resolves its loggers at import, before this runs, so what this proves along the
    way is that the proxy picks the provider up afterwards, exactly as an application that
    configures OpenTelemetry after importing the library would.
    """
    collected = InMemoryLogRecordExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(collected))
    set_logger_provider(provider)
    return collected


@pytest.fixture
def records(log_exporter: InMemoryLogRecordExporter) -> Iterator[Logged]:
    """What one test emitted on the logs signal, with earlier tests' records cleared away."""
    log_exporter.clear()
    yield Logged(log_exporter)
    log_exporter.clear()
