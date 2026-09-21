"""The product: one verb, `ask`, over any shape of question.

`Guide` and `AsyncGuide` are the same object twice. Every decision either of them makes
is made by `_prepare` and `_finish`, which are module functions with no transport in
them; what is written out twice is the two `await`s. That is what keeps the synchronous
and the asyncio surfaces from drifting, and it is why the test suite runs every wire and
tracing assertion through both.
"""

import os
from collections.abc import Awaitable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Self, final, override

from opentelemetry.trace import Span

from guideme._ask_overloads import AsyncAskOverloads, SyncAskOverloads
from guideme._json import Json, dumps
from guideme.api import (
    ModelEntry,
    Request,
    Response,
    answer_from_wire,
    question_to_wire,
    request_to_wire,
)
from guideme.api.client import DEFAULT_BASE_URL, AsyncClient, Client, Endpoint, RetryPolicy
from guideme.ask import Claim, Plan, decode, encode
from guideme.errors import ConfigError, GuidemeError, ProtocolError
from guideme.policy import Outcome, Policy, Thresholds, resolve
from guideme.scalars import ApiKey, Model
from guideme.telemetry import (
    EVENT_MODES,
    Events,
    answer_event,
    ask_span,
    fail_ask,
    logs_available,
    logs_missing,
    record_response,
)

KEY_VAR = "TYPESAFE_API_KEY"
"""Where `from_env` reads the key. Required; nothing else stands in for it."""

BASE_URL_VAR = "TYPESAFE_BASE_URL"
"""Optional origin override for `from_env`."""

MODEL_VAR = "GUIDEME_MODEL"
"""Optional model override for `from_env`."""

DEFAULT_TIMEOUT = timedelta(seconds=30)
"""How long one phase of one attempt may take.

`httpx` spends this budget per phase, not per attempt: connecting, writing, reading and
waiting for a pooled connection each get the whole of it, so one attempt can take several
of these before it gives up. Rust's `reqwest` deadline covers the attempt instead, and the
two SDKs differ here on purpose rather than by oversight. Worst case is
`(max_retries + 1)` attempts, plus the backoff between them.
"""


@final
@dataclass(frozen=True, slots=True)
class ModelInfo:
    """One model the account may use, as `GET /v1/models` describes it.

    A plain value object rather than the wire model, so a caller never has to name a
    pydantic type to hold what `models()` returned.
    """

    name: str
    description: str
    release_date: str


@final
@dataclass(frozen=True, slots=True)
class _Config:
    """What a guide carries besides its client.

    Not where the answers and retries go: that is one setting used on both sides of the
    seam, so the client owns it and the guide reads `client.events` back, the same way it
    reads `client.endpoint`.
    """

    model: Model
    policy: Policy
    record_state: bool


@final
@dataclass(frozen=True, slots=True)
class _Prepared:
    """One ask, planned: what to send, what to read it back as, and against what."""

    claim: Claim
    request: Request
    state_text: str
    state_bytes: int
    thresholds: Mapping[str, Thresholds]


def _prepare(shape: object, state: Json, config: _Config) -> _Prepared:
    """Plan one ask.

    Runs before any span is opened, so the errors it raises, an empty batch or a state
    that is not JSON-shaped, are never recorded on one. Both are the caller's mistake
    and neither reached the API.

    Every question's instructions go through the same serialiser the state does, and its
    result is thrown away: a `NaN`, an infinity or a value `json` cannot represent is a
    `ConfigError` here rather than a `null` the API reads as an absent field.
    """
    state_text = dumps(state, "state")
    plan = Plan(base=config.policy)
    claim = encode(shape, plan)
    if not plan.specs:
        detail = "a batch needs at least one question"
        raise ConfigError(detail)
    for qid, (instructions, _spec) in plan.specs.items():
        _ = dumps(instructions, f"question {qid} instructions")
    questions = {
        qid: question_to_wire(instructions, spec)
        for qid, (instructions, spec) in plan.specs.items()
    }
    return _Prepared(
        claim=claim,
        request=request_to_wire(state, config.model.name, questions),
        state_text=state_text,
        state_bytes=len(state_text.encode()),
        thresholds=plan.thresholds,
    )


def _open(prepared: _Prepared, config: _Config, endpoint: Endpoint) -> AbstractContextManager[Span]:
    """Open the ask span. The state's length is always recorded; its content only on request."""
    return ask_span(
        config.model.name,
        endpoint.host,
        endpoint.port,
        len(prepared.thresholds),
        prepared.state_bytes,
        prepared.state_text if config.record_state else None,
    )


def _finish(span: Span, prepared: _Prepared, response: Response, events: Events) -> object:
    """Read one response back into the shape the caller handed in.

    One event per question, emitted from the resolved outcome in encounter order, before
    the unsure ladder gets to pick anything. A batch is atomic: one bad answer fails it.
    """
    record_response(span, response.model, response.usage.input_tokens, response.usage.output_tokens)
    reply: dict[str, tuple[Outcome, Thresholds]] = {}
    for qid, thresholds in prepared.thresholds.items():
        answer = response.answers.get(qid)
        if answer is None:
            detail = f"no answer for question {qid}"
            raise ProtocolError(detail)
        outcome = resolve(answer_from_wire(answer), thresholds)
        answer_event(span, qid, outcome, thresholds, events)
        reply[qid] = (outcome, thresholds)
    return decode(prepared.claim, reply)


def _merged(config: _Config, policy: Policy) -> _Config:
    """Patch a policy over a guide's, settling it now so a bad patch fails where it is written."""
    merged = policy.over(config.policy)
    _ = merged.settle()
    return replace(config, policy=merged)


def _described(entries: list[ModelEntry]) -> tuple[ModelInfo, ...]:
    """The wire's model list as value objects."""
    return tuple(
        ModelInfo(name=entry.name, description=entry.description, release_date=entry.release_date)
        for entry in entries
    )


def _describe(kind: str, config: _Config, endpoint: Endpoint) -> str:
    """The shared `repr`. The key is a constant here; it is never read to be printed."""
    return (
        f"{kind}(model={config.model.name!r}, base_url={endpoint.base_url!r}, api_key=ApiKey(***))"
    )


@final
class Guide(SyncAskOverloads):
    """A configured entry point to Jev. Build one and share it; it owns a connection pool."""

    def __init__(self, client: Client, config: _Config) -> None:
        """Wrap a built client. Use `Guide.from_env` or `Guide.builder` instead."""
        self._client = client
        self._config = config

    @staticmethod
    def from_env() -> "Guide":
        """Read `TYPESAFE_API_KEY`, and `TYPESAFE_BASE_URL` and `GUIDEME_MODEL` if they are set."""
        return GuideBuilder().from_env().build()

    @staticmethod
    def builder() -> "GuideBuilder":
        """Start configuring a guide."""
        return GuideBuilder()

    def with_policy(self, policy: Policy) -> "Guide":
        """A guide sharing this client, with `policy` patched over this one's.

        The connection pool is shared, so `close()` on either guide closes it for both.
        """
        return Guide(self._client, _merged(self._config, policy))

    def models(self) -> tuple[ModelInfo, ...]:
        """The models this account may use. No ask span of its own."""
        return _described(self._client.models())

    def close(self) -> None:
        """Close the connection pool."""
        self._client.close()

    @override
    def __repr__(self) -> str:
        return _describe("Guide", self._config, self._client.endpoint)

    @override
    def _ask(self, shape: object, state: Json) -> object:
        prepared = _prepare(shape, state, self._config)
        with _open(prepared, self._config, self._client.endpoint) as span:
            try:
                response = self._client.evaluate(prepared.request)
                decoded = _finish(span, prepared, response, self._client.events)
            except GuidemeError as error:
                fail_ask(span, error)
                raise
            return decoded


@final
class AsyncGuide(AsyncAskOverloads):
    """A configured entry point to Jev for `asyncio`. The same surface as `Guide`."""

    def __init__(self, client: AsyncClient, config: _Config) -> None:
        """Wrap a built client. Use `AsyncGuide.from_env` or `AsyncGuide.builder` instead."""
        self._client = client
        self._config = config

    @staticmethod
    def from_env() -> "AsyncGuide":
        """Read `TYPESAFE_API_KEY`, and `TYPESAFE_BASE_URL` and `GUIDEME_MODEL` if they are set."""
        return GuideBuilder().from_env().build_async()

    @staticmethod
    def builder() -> "GuideBuilder":
        """Start configuring a guide."""
        return GuideBuilder()

    def with_policy(self, policy: Policy) -> "AsyncGuide":
        """A guide sharing this client, with `policy` patched over this one's.

        The connection pool is shared, so `close()` on either guide closes it for both.
        """
        return AsyncGuide(self._client, _merged(self._config, policy))

    async def models(self) -> tuple[ModelInfo, ...]:
        """The models this account may use. No ask span of its own."""
        return _described(await self._client.models())

    async def close(self) -> None:
        """Close the connection pool."""
        await self._client.close()

    @override
    def __repr__(self) -> str:
        return _describe("AsyncGuide", self._config, self._client.endpoint)

    @override
    def _ask(self, shape: object, state: Json) -> Awaitable[object]:
        """Match the base exactly: a plain call returning something awaitable."""
        return self._answer(shape, state)

    async def _answer(self, shape: object, state: Json) -> object:
        prepared = _prepare(shape, state, self._config)
        with _open(prepared, self._config, self._client.endpoint) as span:
            try:
                response = await self._client.evaluate(prepared.request)
                decoded = _finish(span, prepared, response, self._client.events)
            except GuidemeError as error:
                fail_ask(span, error)
                raise
            return decoded


@final
class GuideBuilder:
    """Configures a `Guide` or an `AsyncGuide`. Every setter returns the builder."""

    def __init__(self) -> None:
        """Start from the defaults: the public API, `jev-latest`, 3 retries, a 30 s timeout.

        Answers and retries go to both signals, because a log record costs nothing until
        the application installs a logger provider.
        """
        self._api_key: ApiKey | None = None
        self._base_url: str = DEFAULT_BASE_URL
        self._model: Model = Model.latest()
        self._policy: Policy = Policy()
        self._retry: RetryPolicy = RetryPolicy()
        self._timeout: timedelta = DEFAULT_TIMEOUT
        self._record_state: bool = False
        self._events: Events = "both"

    def api_key(self, key: ApiKey) -> Self:
        """The API key. Required unless the guide is built by `from_env`."""
        self._api_key = key
        return self

    def from_env(self) -> Self:
        """Apply `TYPESAFE_API_KEY`, and `TYPESAFE_BASE_URL` and `GUIDEME_MODEL` if they are set.

        `Guide.from_env()` is this followed by `build()`. Reach for the builder form when a
        setting has no environment variable, as `Guide.builder().from_env().events("log")`
        does: the environment still supplies the key and the origin.

        This is an ordinary setter, so the last write wins and the order of the chain is
        what decides. `.api_key(k).from_env()` replaces `k` with the environment's key;
        `.from_env().base_url(x)` keeps `x`. A variable that is not set writes nothing, so
        `.base_url(x).from_env()` keeps `x` when `TYPESAFE_BASE_URL` is absent and replaces
        it when it is present.

        Raises:
            ConfigError: `TYPESAFE_API_KEY` is not set.
        """
        key = os.environ.get(KEY_VAR)
        if key is None:
            detail = f"{KEY_VAR} is not set"
            raise ConfigError(detail)
        self._api_key = ApiKey(key)
        base_url = os.environ.get(BASE_URL_VAR)
        if base_url is not None:
            self._base_url = base_url
        model = os.environ.get(MODEL_VAR)
        if model is not None:
            self._model = Model(model)
        return self

    def base_url(self, url: str) -> Self:
        """Override the API origin, for a test server or a proxy. It may not carry credentials."""
        self._base_url = url
        return self

    def model(self, model: Model) -> Self:
        """The model or alias; `jev-latest` by default."""
        self._model = model
        return self

    def policy(self, policy: Policy) -> Self:
        """The guide-wide policy patch. A question's own patch still wins over it."""
        self._policy = policy
        return self

    def max_retries(self, count: int) -> Self:
        """Resends for `429` and `529`; 3 by default, `0` to never resend."""
        self._retry = replace(self._retry, max_retries=count)
        return self

    def backoff(self, base: timedelta) -> Self:
        """Base delay of the exponential backoff; 500 ms by default."""
        self._retry = replace(self._retry, backoff=base)
        return self

    def timeout(self, per_attempt: timedelta) -> Self:
        """Timeout for one phase of one attempt; 30 s by default.

        `httpx` applies it to connecting, writing, reading and pool acquisition
        separately rather than as one deadline for the attempt, so an attempt that is
        slow in more than one phase can outlast it. See `DEFAULT_TIMEOUT`.
        """
        if per_attempt <= timedelta():
            detail = f"timeout {per_attempt} is not positive"
            raise ConfigError(detail)
        self._timeout = per_attempt
        return self

    def events(self, where: Events) -> Self:
        """Where an answer and a retry are written: `"span"`, `"log"` or `"both"`.

        `"both"` by default, which is what the Rust SDK emits before its subscriber
        filters anything, and which costs a traces-only application nothing: with no
        logger provider installed the record goes to OpenTelemetry's no-op logger. An
        application exporting traces and logs to the same backend sets `"span"` or
        `"log"` so each event is stored once.

        `"span"` needs nothing but the traces API. `"log"` and `"both"` need the logs
        API, which `opentelemetry-api` keeps private, so asking for either where the
        installed release has none is refused here rather than silently dropping every
        record.

        Raises:
            ConfigError: `where` is not one of the three modes, or it asks for log
                records and this `opentelemetry-api` provides no logs API.
        """
        if where not in EVENT_MODES:
            detail = f"events {where!r} is not one of {', '.join(sorted(EVENT_MODES))}"
            raise ConfigError(detail)
        if where != "span" and not logs_available():
            raise ConfigError(logs_missing(where))
        self._events = where
        return self

    def record_state(self, on: bool) -> Self:  # noqa: FBT001 -- record_state(True) is the surface
        """Record the state JSON on the ask span. Off by default: the state is user data."""
        self._record_state = on
        return self

    def build(self) -> Guide:
        """Build a synchronous guide, validating the policy and the origin now."""
        key, endpoint, config = self._settle()
        return Guide(Client(key, endpoint, self._retry, self._timeout, self._events), config)

    def build_async(self) -> AsyncGuide:
        """Build an asyncio guide, validating the policy and the origin now."""
        key, endpoint, config = self._settle()
        client = AsyncClient(key, endpoint, self._retry, self._timeout, self._events)
        return AsyncGuide(client, config)

    def _settle(self) -> tuple[ApiKey, Endpoint, _Config]:
        """Everything both builds need, with every check done before a socket is opened."""
        if self._api_key is None:
            detail = "api_key is required"
            raise ConfigError(detail)
        _ = self._policy.settle()
        return (
            self._api_key,
            Endpoint.parse(self._base_url),
            _Config(model=self._model, policy=self._policy, record_state=self._record_state),
        )
