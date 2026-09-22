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
from guideme.api.client import (
    DEFAULT_BASE_URL,
    AsyncClient,
    AsyncTransport,
    Client,
    Endpoint,
    RetryPolicy,
    Transport,
)
from guideme.ask import Claim, Plan, decode, encode
from guideme.errors import ConfigError, GuidemeError, ProtocolError
from guideme.policy import Outcome, Policy, Thresholds, resolve
from guideme.receipt import Receipt
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


def _receipt(answer: object, response: Response) -> Receipt[object]:
    """Put one answered shape beside what the response said it cost and what produced it."""
    return Receipt(answer=answer, model=response.model, usage=response.usage)


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
    """A configured entry point to Jev. Build one and share it; it owns a connection pool.

    Share it across threads: a guide is frozen configuration over one `httpx.Client`, and
    both are safe to use from several threads at once. One guide asking concurrently is
    what the pool is for. Building one per request works and opens a pool per request,
    which is the cost the pool exists to avoid. Close it once, by hand or by leaving a
    `with` block; a guide derived with `with_policy` is a second holder of the same pool
    and closing it leaves this one able to ask.
    """

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

        The connection pool is shared and counted: the guide returned here is a second
        holder of it, so closing either one leaves the other able to ask and the pool
        closes when the last of them does. Close each guide once.
        """
        return Guide(self._client.share(), _merged(self._config, policy))

    def __enter__(self) -> Self:
        """Enter a `with` block. The guide is ready to ask before this; nothing is opened here."""
        return self

    def __exit__(self, kind: object, error: object, traceback: object) -> None:
        """Leave a `with` block by closing this guide. Nothing is suppressed."""
        self.close()

    def models(self) -> tuple[ModelInfo, ...]:
        """The models this account may use. No ask span of its own.

        One `GET /v1/models`, resent on exactly the terms an ask is: a `429` or a `529`
        waits out the backoff and goes again, and a connection failure does too. A `429`
        while a process is starting up therefore does not fail the start.

        Raises:
            AuthError: the key was missing or rejected.
            InvalidError: the API rejected the request (`422`).
            RateLimitedError: still throttled after the retries (`429`).
            OverloadedError: still overloaded after the retries (`529`).
            TransportError: the request never completed.
            UnexpectedStatusError: any other status.
            ProtocolError: the body did not match the contract.
        """
        return _described(self._client.models())

    def close(self) -> None:
        """Close the connection pool."""
        self._client.close()

    @override
    def __repr__(self) -> str:
        return _describe("Guide", self._config, self._client.endpoint)

    @override
    def _ask(self, shape: object, state: Json) -> object:
        return self._ask_with_receipt(shape, state).answer

    @override
    def _ask_with_receipt(self, shape: object, state: Json) -> Receipt[object]:
        prepared = _prepare(shape, state, self._config)
        with _open(prepared, self._config, self._client.endpoint) as span:
            try:
                response = self._client.evaluate(prepared.request)
                decoded = _finish(span, prepared, response, self._client.events)
            except GuidemeError as error:
                fail_ask(span, error)
                raise
            return _receipt(decoded, response)


@final
class AsyncGuide(AsyncAskOverloads):
    """A configured entry point to Jev for `asyncio`. The same surface as `Guide`.

    Share it across tasks: a guide is frozen configuration over one `httpx.AsyncClient`,
    and concurrent asks from one guide are what the pool is for — `asyncio.gather` over
    a batch of them is the intended shape. It belongs to the event loop it was built on.
    Close it once, by hand or by leaving an `async with` block; a guide derived with
    `with_policy` is a second holder of the same pool and closing it leaves this one able
    to ask.
    """

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

        The connection pool is shared and counted: the guide returned here is a second
        holder of it, so closing either one leaves the other able to ask and the pool
        closes when the last of them does. Close each guide once.
        """
        return AsyncGuide(self._client.share(), _merged(self._config, policy))

    async def __aenter__(self) -> Self:
        """Enter an `async with` block. Nothing is opened here; the guide is already ready."""
        return self

    async def __aexit__(self, kind: object, error: object, traceback: object) -> None:
        """Leave an `async with` block by closing this guide. Nothing is suppressed."""
        await self.close()

    async def models(self) -> tuple[ModelInfo, ...]:
        """The models this account may use. No ask span of its own.

        One `GET /v1/models`, resent on exactly the terms an ask is: a `429` or a `529`
        waits out the backoff and goes again, and a connection failure does too. A `429`
        while a process is starting up therefore does not fail the start.

        Raises:
            AuthError: the key was missing or rejected.
            InvalidError: the API rejected the request (`422`).
            RateLimitedError: still throttled after the retries (`429`).
            OverloadedError: still overloaded after the retries (`529`).
            TransportError: the request never completed.
            UnexpectedStatusError: any other status.
            ProtocolError: the body did not match the contract.
        """
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

    @override
    def _ask_with_receipt(self, shape: object, state: Json) -> Awaitable[Receipt[object]]:
        """Match the base exactly: a plain call returning something awaitable."""
        return self._answer_with_receipt(shape, state)

    async def _answer(self, shape: object, state: Json) -> object:
        return (await self._answer_with_receipt(shape, state)).answer

    async def _answer_with_receipt(self, shape: object, state: Json) -> Receipt[object]:
        prepared = _prepare(shape, state, self._config)
        with _open(prepared, self._config, self._client.endpoint) as span:
            try:
                response = await self._client.evaluate(prepared.request)
                decoded = _finish(span, prepared, response, self._client.events)
            except GuidemeError as error:
                fail_ask(span, error)
                raise
            return _receipt(decoded, response)


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
        # `None` is "the caller never said", which is not the same as "the default", and
        # only the first of the two may sit beside an injected transport.
        self._timeout: timedelta | None = None
        self._transport: Transport | None = None
        self._async_transport: AsyncTransport | None = None
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
        """Override the API origin, for a test server or a proxy. It may not carry credentials.

        Unlike the other setters this one stores the string and checks it later:
        a malformed origin, or one carrying a user or a password, is a
        `ConfigError` from `build()` or `build_async()`, not from here.
        """
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
        """Resends for `429` and `529`; 3 by default, `0` to never resend.

        Raises:
            ConfigError: `count` is negative.
        """
        self._retry = replace(self._retry, max_retries=count)
        return self

    def backoff(self, base: timedelta) -> Self:
        """Base delay of the exponential backoff; 500 ms by default.

        Raises:
            ConfigError: `base` is negative.
        """
        self._retry = replace(self._retry, backoff=base)
        return self

    def timeout(self, per_attempt: timedelta) -> Self:
        """Timeout for one phase of one attempt; 30 s by default.

        **This is not a deadline for the attempt.** `httpx` gives the whole budget to
        each phase separately — connecting, writing, reading, and waiting for a pooled
        connection — so one attempt that is slow in more than one phase takes longer
        than `per_attempt` and is not in breach of anything. Worst case for a call is
        `(max_retries + 1)` attempts of several phases each, plus the backoff between
        them. Rust's `reqwest` deadline covers the attempt as a whole instead; the two
        SDKs differ here because their HTTP clients do, and `docs/contract.md` records
        it as a divergence rather than leaving a reader to find it.

        A timeout belongs to the transport that honours it, so this and
        `transport(...)`/`async_transport(...)` refuse each other in whichever order
        they are written. An injected transport decides its own deadlines, and `httpx`
        hands it this budget as a request extension it is free to ignore — as
        `httpx.MockTransport` does — so accepting both would promise a timeout that
        nothing applies.

        Raises:
            ConfigError: `per_attempt` is zero or negative, or a transport is already set.
        """
        if per_attempt <= timedelta():
            detail = f"timeout {per_attempt} is not positive"
            raise ConfigError(detail)
        if self._transport is not None or self._async_transport is not None:
            detail = "timeout and an injected transport conflict; the transport owns its deadlines"
            raise ConfigError(detail)
        self._timeout = per_attempt
        return self

    def transport(self, transport: Transport) -> Self:
        """Send through this `httpx.BaseTransport` rather than one `httpx` opens.

        A proxy, a client certificate, or an `httpx.MockTransport` that answers a test
        without a socket — the README's "Testing your code" section is the last of those
        written out. It is used by `build()`; `build_async()` needs `async_transport`.

        Raises:
            ConfigError: `timeout(...)` is already set. See `timeout` for why.
        """
        self._refuse_a_timeout("transport")
        self._transport = transport
        return self

    def async_transport(self, transport: AsyncTransport) -> Self:
        """Send through this `httpx.AsyncBaseTransport` rather than one `httpx` opens.

        The asyncio half of `transport`, used by `build_async()`. `httpx.MockTransport`
        is both kinds at once, so one of those can be given to either setter.

        Raises:
            ConfigError: `timeout(...)` is already set. See `timeout` for why.
        """
        self._refuse_a_timeout("async_transport")
        self._async_transport = transport
        return self

    def _refuse_a_timeout(self, setter: str) -> None:
        """Refuse a transport written after a timeout, as `timeout` refuses the other order."""
        if self._timeout is not None:
            detail = f"{setter} and timeout conflict; the transport owns its deadlines"
            raise ConfigError(detail)

    def events(self, where: Events) -> Self:
        """Where an answer and a retry are written: `"span"`, `"log"` or `"both"`.

        `"both"` by default, which is what the Rust SDK emits before its subscriber
        filters anything, and which costs a traces-only application nothing: with no
        logger provider installed the record goes to OpenTelemetry's no-op logger. An
        application exporting traces and logs to the same backend sets `"span"` or
        `"log"` so each event is stored once. `docs/observability.md` has the table.

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
        """Build a synchronous guide, validating the policy and the origin now.

        Raises:
            ConfigError: no API key was set, the `base_url` is malformed or
                carries credentials, the policy's thresholds are out of range, or
                `async_transport(...)` was set, which only `build_async` can use.
        """
        if self._async_transport is not None:
            detail = "build() cannot use an async_transport; use build_async() or transport()"
            raise ConfigError(detail)
        key, endpoint, config = self._settle()
        client = Client(
            key, endpoint, self._retry, self._settled_timeout(), self._events, self._transport
        )
        return Guide(client, config)

    def build_async(self) -> AsyncGuide:
        """Build an asyncio guide, validating the policy and the origin now.

        Raises:
            ConfigError: no API key was set, the `base_url` is malformed or
                carries credentials, the policy's thresholds are out of range, or
                `transport(...)` was set, which only `build` can use.
        """
        if self._transport is not None:
            detail = "build_async() cannot use a transport; use build() or async_transport()"
            raise ConfigError(detail)
        key, endpoint, config = self._settle()
        client = AsyncClient(
            key, endpoint, self._retry, self._settled_timeout(), self._events, self._async_transport
        )
        return AsyncGuide(client, config)

    def _settled_timeout(self) -> timedelta:
        """The timeout to build with: the caller's, or the default they never overrode."""
        return DEFAULT_TIMEOUT if self._timeout is None else self._timeout

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
