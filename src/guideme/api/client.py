"""The only module that touches HTTP.

Retries `429` and `529` with exponential backoff, honouring `retry-after`, and maps
every status to a typed error. Each attempt is one span shaped by the OpenTelemetry
HTTP client conventions, so a retried request is sibling spans under the ask span,
each with its own status code, and one that is about to be resent also carries a retry
event.

`Client` and `AsyncClient` are the same flow twice. Everything either of them decides
is decided by `step`, which is pure; what is written out twice is the `await` and the
sleep.
"""

import asyncio
import time
from collections.abc import Callable
from copy import copy
from dataclasses import dataclass, field
from datetime import timedelta
from random import SystemRandom
from threading import Lock
from typing import Self, final

import httpx
from opentelemetry.trace import Span
from pydantic import ValidationError

from guideme.api import ModelEntry, ModelsResponse, Request, Response, validation_detail
from guideme.errors import (
    AuthError,
    ConfigError,
    GuidemeError,
    InvalidError,
    OverloadedError,
    ProtocolError,
    RateLimitedError,
    TransportError,
    UnexpectedStatusError,
)
from guideme.scalars import ApiKey
from guideme.telemetry import Events, attempt_span, fail_attempt, record_status, retry_event

DEFAULT_BASE_URL = "https://api.typesafe.ai"
"""Where requests go unless the caller says otherwise."""

EVALUATE = "/v1/systemone"
"""The judging endpoint, and its low-cardinality `url.template`."""

MODELS = "/v1/models"
"""The model-listing endpoint, and its `url.template`."""

MAX_BACKOFF = timedelta(seconds=30)
"""The longest guideme waits between attempts, and the longest `retry-after` it honours."""

JITTER = timedelta(milliseconds=250)
"""Added to each backoff so a fleet retrying together spreads out."""

OK = 200
"""The contract's only success.

Redirects are followed by the client, so a `3xx` never reaches this code; anything else
that does is an error. `httpx` drops the authorization header when a redirect changes
origin, which is what keeps a redirected key from leaving the API's own host.
"""

UNAUTHORIZED = 401
UNPROCESSABLE = 422
TOO_MANY_REQUESTS = 429
OVERLOADED = 529
RETRYABLE = frozenset({TOO_MANY_REQUESTS, OVERLOADED})
"""The two statuses guideme resends after. Everything else fails on the first attempt."""

type Transport = httpx.BaseTransport
"""A caller-supplied synchronous transport, as `GuideBuilder.transport` takes one.

Named here so that `guideme.guide` can offer the setter without importing `httpx`: this
module stays the package's only importer of it, and a caller still writes
`httpx.MockTransport` or `httpx.HTTPTransport` in their own code.
"""

type AsyncTransport = httpx.AsyncBaseTransport
"""A caller-supplied asyncio transport, named here for the same reason as `Transport`."""

_KNOWN_PORTS = {"http": 80, "https": 443}
_CAP_EXPONENT = 20
_random = SystemRandom()


@final
@dataclass(frozen=True, slots=True)
class Endpoint:
    """Where requests go, validated once.

    `url.full` goes on every attempt span, so a base URL carrying credentials is
    refused here rather than recorded there.
    """

    base_url: str
    host: str
    port: int

    @classmethod
    def parse(cls, base_url: str) -> Self:
        """Validate an origin. Raises `ConfigError` without a host or port, or with credentials."""
        trimmed = base_url.rstrip("/")
        try:
            parsed = httpx.URL(trimmed)
        except httpx.InvalidURL as error:
            detail = f"base_url {base_url!r}: {error}"
            raise ConfigError(detail) from error
        if parsed.userinfo:
            detail = "base_url must not carry credentials; use the api key"
            raise ConfigError(detail)
        port = _KNOWN_PORTS.get(parsed.scheme) if parsed.port is None else parsed.port
        if not parsed.host or port is None:
            detail = f"base_url {base_url!r} needs a host and a port"
            raise ConfigError(detail)
        return cls(base_url=trimmed, host=parsed.host, port=port)

    def url(self, path: str) -> str:
        """The absolute URL of one endpoint path."""
        return f"{self.base_url}{path}"


@final
@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How often to resend a request that failed, and how long to wait first."""

    max_retries: int = 3
    backoff: timedelta = timedelta(milliseconds=500)

    def __post_init__(self) -> None:
        """Validate: neither the retry count nor the base delay may be negative."""
        if self.max_retries < 0:
            detail = f"max_retries {self.max_retries} is negative"
            raise ConfigError(detail)
        if self.backoff < timedelta():
            detail = f"backoff {self.backoff} is negative"
            raise ConfigError(detail)

    def delay(self, attempt: int) -> timedelta:
        """Exponential backoff with jitter: `backoff * 2^attempt`, capped, plus up to 250 ms.

        The exponent is capped too. The result is clamped to `MAX_BACKOFF` either way,
        and an uncapped shift would overflow before it changed the answer.
        """
        exponential = min(self.backoff * 2 ** min(attempt, _CAP_EXPONENT), MAX_BACKOFF)
        return exponential + JITTER * _random.random()


@final
@dataclass(slots=True)
class _Pool[T]:
    """One `httpx` client, the number of clients holding it, and the lock over both.

    A guide derived with `with_policy` shares the pool its parent opened, and Python has
    no `Arc` to count that for us. So the count is explicit: every holder releases once,
    and the transport closes when the last one does. Closing a derived guide therefore
    leaves its parent able to ask, which is what makes `with` safe on both.

    The count alone is not enough, and `Client.close` is where the rest of it lives: a
    holder that is closed twice must release once. Each `share()` hands back a distinct
    client carrying its own flag for that, so no count kept here can be spent by the
    wrong holder. The clamp in `drop` is the last line of defence rather than the
    mechanism.

    **`guard` covers the count and every holder's flag together.** `Guide`'s docstring
    says to share a guide across threads, so two threads closing two guides over one pool
    is a documented thing to do; a flag read, a flag flip and a decrement are three steps,
    and interleaving them either closes a live transport or leaks it. Hold `guard` across
    all three. `take` and `drop` therefore do not lock: their caller is already inside it,
    and a lock taken twice would deadlock. Nothing slow happens under it — `httpx`'s own
    close is called after it is released, and no `await` is ever reached while it is held,
    so the asyncio client can use the same plain lock as the synchronous one.

    `ask` never touches any of this. It reads `http` and nothing else, so the pool's lock
    is taken once when a guide is derived and once when one is closed, never per request.
    """

    http: T
    holders: int = 1
    guard: Lock = field(default_factory=Lock)

    def take(self) -> None:
        """Take one more hold, for a client derived from one already holding it.

        Call with `guard` held.
        """
        self.holders += 1

    def drop(self) -> bool:
        """Drop one hold. True when this was the last, so the transport must be closed.

        Call with `guard` held. Every caller is a `Client.close` that has just flipped its
        own flag to spent in the same critical section, so in practice the count never
        reaches here already at zero. It is clamped anyway: returning `True` off a
        negative count would close a live transport.
        """
        if not self.holders:
            return False
        self.holders -= 1
        return not self.holders


@final
@dataclass(frozen=True, slots=True)
class Retry:
    """Resend, after waiting this long."""

    delay: timedelta


@final
@dataclass(frozen=True, slots=True)
class Ready:
    """The attempt succeeded. The body is the caller's to read, inside the attempt's span."""

    body: str


type Step = Ready | Retry | GuidemeError
"""What one attempt concluded: a body to read, a resend, or the error to raise."""


def step(
    status: int, body: str, retry_after: timedelta | None, attempt: int, retry: RetryPolicy
) -> Step:
    """Classify one attempt that got a response.

    Pure: no I/O, no sleeping, no telemetry. A `retry-after` longer than `MAX_BACKOFF`
    is not waited for; the call fails carrying that duration so the caller decides.

    Both endpoints go through this, so a `429` on `GET /v1/models` is resent on exactly
    the terms one on `POST /v1/systemone` is. The API's own docs say an SDK handles a
    `429` for you, and saying it of one endpoint only would be a promise with a hole in it.
    """
    if status == OK:
        return Ready(body)
    if status not in RETRYABLE:
        return _classify(status, body)
    if attempt >= retry.max_retries or (retry_after is not None and retry_after > MAX_BACKOFF):
        if status == TOO_MANY_REQUESTS:
            return RateLimitedError(retry_after)
        return OverloadedError(retry_after)
    return Retry(retry.delay(attempt) if retry_after is None else retry_after)


def resend_after(error: httpx.HTTPError, attempt: int, retry: RetryPolicy) -> timedelta | None:
    """How long to wait before resending a request that never got a response.

    `None` means do not resend, for either of two reasons: the failure was not a
    connection failure, or the budget for them is spent. The budget is the one `step`
    spends on a `429`, so a call cannot exceed `max_retries + 1` attempts by mixing them.

    Only `httpx.ConnectError` is resent: a refused or reset connection, or a TLS handshake
    that failed. The request never reached a server, so nothing was judged and nothing is
    repeated by trying again. A read timeout and a disconnect part-way through a response
    mean the opposite: the request arrived, the API may have answered it, and a resend
    would ask for the same judgment a second time. A body that fails to decode is not here
    at all; it arrived, and it is a `ProtocolError`.

    **A timeout is never resent, whatever phase it names.** `httpx.ConnectTimeout` looks
    like a connection failure and is excluded anyway, for two reasons. Rust has no such
    case to exclude: `reqwest` sets one deadline over the whole attempt, so a connect-phase
    timeout there is `is_timeout()` and not `is_connect()`, and retrying one here would be
    the two SDKs disagreeing about the same failure. And a retried timeout multiplies the
    wall time `GuideBuilder.timeout` promises, which is the one number a caller sets to
    bound how long a call may take.
    """
    if not isinstance(error, httpx.ConnectError):
        return None
    if attempt >= retry.max_retries:
        return None
    return retry.delay(attempt)


def _evaluated(body: str) -> Response:
    """Read one `POST /v1/systemone` body. Raises `ProtocolError` when it is not one."""
    try:
        return Response.model_validate_json(body)
    except ValidationError as error:
        detail = f"response body: {validation_detail(error)}"
        raise ProtocolError(detail) from error


def _listed(body: str) -> list[ModelEntry]:
    """Read one `GET /v1/models` body. Raises `ProtocolError` when it is not one."""
    try:
        return ModelsResponse.model_validate_json(body).models
    except ValidationError as error:
        detail = f"models body: {validation_detail(error)}"
        raise ProtocolError(detail) from error


def _classify(status: int, body: str) -> GuidemeError:
    if status == UNAUTHORIZED:
        return AuthError()
    if status == UNPROCESSABLE:
        return InvalidError(body)
    return UnexpectedStatusError(status, body)


def _retry_after(headers: httpx.Headers) -> timedelta | None:
    """Read `retry-after` as whole seconds. Anything else is treated as absent, like Rust's."""
    raw = headers.get("retry-after")
    if raw is None:
        return None
    seconds = raw.strip()
    if not (seconds.isascii() and seconds.isdecimal()):
        return None
    return timedelta(seconds=int(seconds))


def _auth(api_key: ApiKey) -> dict[str, str]:
    """The one place the key is read, to build the one header that carries it.

    `ApiKey._expose` is this package's stand-in for Rust's `pub(crate) expose`: off the
    published surface, reachable from the module that has to send the key. Both checkers
    are told about that here, and this is the only call.

    Called once per request rather than once per client, so the bearer string lives only
    as long as the request that carries it. A client holds the `ApiKey`, which prints as
    `ApiKey(***)`, so a `vars()`, a debugger or a crash reporter finds nothing to read.
    """
    # pylint: disable=protected-access  # the sole reader, as _expose's docstring says
    key = api_key._expose()  # noqa: SLF001 # pyright: ignore[reportPrivateUsage] -- sole reader
    return {"authorization": f"Bearer {key}"}


def _sending(api_key: ApiKey) -> dict[str, str]:
    """The headers of one `POST`: the bearer, and the one content type the API takes."""
    return {**_auth(api_key), "content-type": "application/json"}


def _scrub(error: httpx.HTTPError) -> None:
    """Drop the bearer from the failed request an `httpx` error carries.

    The error is chained onto the `TransportError` as its `__cause__`, so the request it
    holds travels with every traceback and every crash report, and that request carries
    the header `_auth` just built. The failed attempt is over, so nothing needs it.
    """
    if not isinstance(error, httpx.RequestError):
        return
    try:
        request = error.request
    except RuntimeError:
        # httpx raises rather than returning None when the error carries no request.
        return
    _ = request.headers.pop("authorization", None)


def _read[T](span: Span, body: str, decode: Callable[[str], T]) -> T:
    """Read one successful body, marking the attempt's span when it is not the shape promised."""
    try:
        return decode(body)
    except ProtocolError as failure:
        fail_attempt(span, failure.kind)
        raise


def _transport(span: Span, error: httpx.HTTPError) -> TransportError:
    """Mark the attempt span for a request that never got a response, and scrub its key."""
    _scrub(error)
    failure = TransportError(error)
    fail_attempt(span, failure.kind)
    return failure


@final
class Client:
    """A synchronous client for the TypeSafe HTTP API. `Guide` builds one for you."""

    def __init__(
        self,
        api_key: ApiKey,
        endpoint: Endpoint,
        retry: RetryPolicy,
        timeout: timedelta,
        events: Events,
        transport: Transport | None = None,
    ) -> None:
        """Open the connection pool. The key is held as an `ApiKey`, never as a header.

        `events` is read back by the guide that owns this client, so the routing knob has
        one home: this client emits the retries under it, the guide emits the answers.

        `transport` is the caller's, when they supplied one; `None` leaves `httpx` to
        build its own. The builder refuses a transport beside a timeout, so the two never
        arrive together.
        """
        self.endpoint = endpoint
        # Declared, because pyright widens a `Literal` inferred from an assignment.
        self.events: Events = events
        self._retry = retry
        self._api_key = api_key
        self._pool = _Pool(
            httpx.Client(
                timeout=timeout.total_seconds(), follow_redirects=True, transport=transport
            )
        )
        self._holding = True

    def share(self) -> "Client":
        """A second client over this one's pool, for a guide derived from this one's.

        A distinct object rather than `self`, because the hold is what has to be released
        exactly once and `self` cannot carry two of them. Two guides over one returned
        `self` would share one flag, so closing the first guide twice would spend the
        second guide's hold and shut the pool under it.

        Everything else is shared: the same pool, key, endpoint, retry policy and event
        routing, so the copy costs nothing and no setting can drift between the two. The
        copy carries this client's own flag, which is why a closed one is refused rather
        than copied: sharing from it would hand back a client holding nothing, over a
        pool that may already be shut, and the mistake would only surface on the first ask.

        Raises:
            ConfigError: this client has already been closed.
        """
        with self._pool.guard:
            if not self._holding:
                detail = "cannot share a closed client"
                raise ConfigError(detail)
            self._pool.take()
        return copy(self)

    def evaluate(self, request: Request) -> Response:
        """`POST /v1/systemone`, resent while the API throttles or a connection fails."""
        return self._fetch(EVALUATE, request.model_dump_json(by_alias=True), _evaluated)

    def models(self) -> list[ModelEntry]:
        """`GET /v1/models`, resent on exactly the terms an ask is."""
        return self._fetch(MODELS, None, _listed)

    def _fetch[T](self, path: str, body: str | None, decode: Callable[[str], T]) -> T:
        """The retry loop, which is the whole of what this client decides how to do.

        One span per attempt, one `guideme.retry` before each wait, and the body read
        inside the span it arrived on so a malformed one marks the attempt that carried
        it. Both endpoints run through here: what tells them apart is a body to send.
        """
        url = self.endpoint.url(path)
        method = "GET" if body is None else "POST"
        for attempt in range(self._retry.max_retries + 1):
            with attempt_span(
                method, url, path, self.endpoint.host, self.endpoint.port, attempt
            ) as span:
                try:
                    response = self._send(url, body)
                except httpx.HTTPError as error:
                    delay = resend_after(error, attempt, self._retry)
                    if delay is None:
                        raise _transport(span, error) from error
                    _scrub(error)
                    fail_attempt(span, TransportError.kind)
                    retry_event(span, None, attempt + 1, delay, self.events)
                    time.sleep(delay.total_seconds())
                    continue
                record_status(span, response.status_code)
                outcome = step(
                    response.status_code,
                    response.text,
                    _retry_after(response.headers),
                    attempt,
                    self._retry,
                )
                match outcome:
                    case Ready(body=text):
                        return _read(span, text, decode)
                    case Retry(delay=delay):
                        fail_attempt(span, str(response.status_code))
                        retry_event(span, response.status_code, attempt + 1, delay, self.events)
                        time.sleep(delay.total_seconds())
                    case GuidemeError():
                        fail_attempt(span, str(response.status_code))
                        raise outcome
        # Unreachable: the loop runs at least once and every arm returns or raises.
        raise RateLimitedError(None)

    def _send(self, url: str, body: str | None) -> httpx.Response:
        """One attempt on the wire. The bearer is built here and lives only as long as it."""
        if body is None:
            return self._pool.http.get(url, headers=_auth(self._api_key))
        return self._pool.http.post(url, content=body, headers=_sending(self._api_key))

    def close(self) -> None:
        """Release this client's hold on the pool, closing it when this was the last hold.

        Idempotent: a client closed twice releases once. The flag is per client, so a
        second close here can never spend a hold that belongs to a client `share()`
        handed out.
        """
        with self._pool.guard:
            if not self._holding:
                return
            self._holding = False
            last = self._pool.drop()
        if last:
            self._pool.http.close()


@final
class AsyncClient:
    """The same client for `asyncio`. `AsyncGuide` builds one for you."""

    def __init__(
        self,
        api_key: ApiKey,
        endpoint: Endpoint,
        retry: RetryPolicy,
        timeout: timedelta,
        events: Events,
        transport: AsyncTransport | None = None,
    ) -> None:
        """Open the connection pool. The key is held as an `ApiKey`, never as a header.

        `events` is read back by the guide that owns this client, so the routing knob has
        one home: this client emits the retries under it, the guide emits the answers.

        `transport` is the caller's, when they supplied one; `None` leaves `httpx` to
        build its own. The builder refuses a transport beside a timeout, so the two never
        arrive together.
        """
        self.endpoint = endpoint
        # Declared, because pyright widens a `Literal` inferred from an assignment.
        self.events: Events = events
        self._retry = retry
        self._api_key = api_key
        self._pool = _Pool(
            httpx.AsyncClient(
                timeout=timeout.total_seconds(), follow_redirects=True, transport=transport
            )
        )
        self._holding = True

    def share(self) -> "AsyncClient":
        """A second client over this one's pool. `Client.share` says why it is a new object.

        Raises:
            ConfigError: this client has already been closed.
        """
        with self._pool.guard:
            if not self._holding:
                detail = "cannot share a closed client"
                raise ConfigError(detail)
            self._pool.take()
        return copy(self)

    async def evaluate(self, request: Request) -> Response:
        """`POST /v1/systemone`, resent while the API throttles or a connection fails."""
        return await self._fetch(EVALUATE, request.model_dump_json(by_alias=True), _evaluated)

    async def models(self) -> list[ModelEntry]:
        """`GET /v1/models`, resent on exactly the terms an ask is."""
        return await self._fetch(MODELS, None, _listed)

    async def _fetch[T](self, path: str, body: str | None, decode: Callable[[str], T]) -> T:
        """`Client._fetch` with the two `await`s. Every decision in it is made the same way."""
        url = self.endpoint.url(path)
        method = "GET" if body is None else "POST"
        for attempt in range(self._retry.max_retries + 1):
            with attempt_span(
                method, url, path, self.endpoint.host, self.endpoint.port, attempt
            ) as span:
                try:
                    response = await self._send(url, body)
                except httpx.HTTPError as error:
                    delay = resend_after(error, attempt, self._retry)
                    if delay is None:
                        raise _transport(span, error) from error
                    _scrub(error)
                    fail_attempt(span, TransportError.kind)
                    retry_event(span, None, attempt + 1, delay, self.events)
                    await asyncio.sleep(delay.total_seconds())
                    continue
                record_status(span, response.status_code)
                outcome = step(
                    response.status_code,
                    response.text,
                    _retry_after(response.headers),
                    attempt,
                    self._retry,
                )
                match outcome:
                    case Ready(body=text):
                        return _read(span, text, decode)
                    case Retry(delay=delay):
                        fail_attempt(span, str(response.status_code))
                        retry_event(span, response.status_code, attempt + 1, delay, self.events)
                        await asyncio.sleep(delay.total_seconds())
                    case GuidemeError():
                        fail_attempt(span, str(response.status_code))
                        raise outcome
        # Unreachable: the loop runs at least once and every arm returns or raises.
        raise RateLimitedError(None)

    async def _send(self, url: str, body: str | None) -> httpx.Response:
        """One attempt on the wire. The bearer is built here and lives only as long as it."""
        if body is None:
            return await self._pool.http.get(url, headers=_auth(self._api_key))
        return await self._pool.http.post(url, content=body, headers=_sending(self._api_key))

    async def close(self) -> None:
        """Release this client's hold on the pool. `Client.close` says why it is idempotent."""
        with self._pool.guard:
            if not self._holding:
                return
            self._holding = False
            last = self._pool.drop()
        if last:
            await self._pool.http.aclose()
