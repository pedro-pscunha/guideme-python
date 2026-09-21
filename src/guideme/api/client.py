"""The only module that touches HTTP.

Retries `429` and `529` with exponential backoff, honouring `retry-after`, and maps
every status to a typed error. Each attempt is one span shaped by the OpenTelemetry
HTTP client conventions, so a retried request is sibling spans under the ask span,
each with its own status code, and a throttled one also carries a retry event.

`Client` and `AsyncClient` are the same flow twice. Everything either of them decides
is decided by `step`, which is pure; what is written out twice is the `await` and the
sleep.
"""

import asyncio
import time
from dataclasses import dataclass
from datetime import timedelta
from random import SystemRandom
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
    """How often to resend a throttled request, and how long to wait first."""

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
@dataclass(frozen=True, slots=True)
class Retry:
    """Resend, after waiting this long."""

    delay: timedelta


type Step = Response | Retry | GuidemeError
"""What one attempt concluded: a decoded response, a resend, or the error to raise."""


def step(
    status: int, body: str, retry_after: timedelta | None, attempt: int, retry: RetryPolicy
) -> Step:
    """Classify one attempt.

    Pure: no I/O, no sleeping, no telemetry. A `retry-after` longer than `MAX_BACKOFF`
    is not waited for; the call fails carrying that duration so the caller decides.
    """
    if status == OK:
        return _decode(body)
    if status not in RETRYABLE:
        return _classify(status, body)
    if attempt >= retry.max_retries or (retry_after is not None and retry_after > MAX_BACKOFF):
        return RateLimitedError(retry_after) if status == TOO_MANY_REQUESTS else OverloadedError()
    return Retry(retry.delay(attempt) if retry_after is None else retry_after)


def _decode(body: str) -> Response | ProtocolError:
    try:
        return Response.model_validate_json(body)
    except ValidationError as error:
        return ProtocolError(f"response body: {validation_detail(error)}")


def _classify(status: int, body: str) -> GuidemeError:
    if status == UNAUTHORIZED:
        return AuthError()
    if status == UNPROCESSABLE:
        return InvalidError(body)
    return UnexpectedStatusError(status, body)


def _attempt_error(status: int, error: GuidemeError) -> str:
    """`error.type` for an attempt span: the status when one arrived, else the error's kind."""
    return error.kind if status == OK else str(status)


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


def _models(span: Span, status: int, body: str, retry_after: timedelta | None) -> list[ModelEntry]:
    """Read one `GET /v1/models` response, marking the span on the way out."""
    if status != OK:
        fail_attempt(span, str(status))
        if status == TOO_MANY_REQUESTS:
            raise RateLimitedError(retry_after)
        if status == OVERLOADED:
            raise OverloadedError
        raise _classify(status, body)
    try:
        return ModelsResponse.model_validate_json(body).models
    except ValidationError as error:
        failure = ProtocolError(f"models body: {validation_detail(error)}")
        fail_attempt(span, failure.kind)
        raise failure from error


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
    ) -> None:
        """Open the connection pool. The key is held as an `ApiKey`, never as a header.

        `events` is read back by the guide that owns this client, so the routing knob has
        one home: this client emits the retries under it, the guide emits the answers.
        """
        self.endpoint = endpoint
        # Declared, because pyright widens a `Literal` inferred from an assignment.
        self.events: Events = events
        self._retry = retry
        self._api_key = api_key
        self._http = httpx.Client(timeout=timeout.total_seconds(), follow_redirects=True)

    def evaluate(self, request: Request) -> Response:
        """`POST /v1/systemone`, resending a `429` or a `529` up to the retry policy's limit."""
        url = self.endpoint.url(EVALUATE)
        body = request.model_dump_json(by_alias=True)
        for attempt in range(self._retry.max_retries + 1):
            with attempt_span(
                "POST", url, EVALUATE, self.endpoint.host, self.endpoint.port, attempt
            ) as span:
                try:
                    response = self._http.post(url, content=body, headers=_sending(self._api_key))
                except httpx.HTTPError as error:
                    raise _transport(span, error) from error
                record_status(span, response.status_code)
                outcome = step(
                    response.status_code,
                    response.text,
                    _retry_after(response.headers),
                    attempt,
                    self._retry,
                )
                match outcome:
                    case Response():
                        return outcome
                    case Retry(delay=delay):
                        fail_attempt(span, str(response.status_code))
                        retry_event(span, response.status_code, attempt + 1, delay, self.events)
                        time.sleep(delay.total_seconds())
                    case GuidemeError():
                        fail_attempt(span, _attempt_error(response.status_code, outcome))
                        raise outcome
        # Unreachable: the loop runs at least once and every arm returns or raises.
        raise RateLimitedError(None)

    def models(self) -> list[ModelEntry]:
        """`GET /v1/models`. Not retried."""
        url = self.endpoint.url(MODELS)
        with attempt_span("GET", url, MODELS, self.endpoint.host, self.endpoint.port, 0) as span:
            try:
                response = self._http.get(url, headers=_auth(self._api_key))
            except httpx.HTTPError as error:
                raise _transport(span, error) from error
            record_status(span, response.status_code)
            return _models(
                span, response.status_code, response.text, _retry_after(response.headers)
            )

    def close(self) -> None:
        """Close the connection pool."""
        self._http.close()


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
    ) -> None:
        """Open the connection pool. The key is held as an `ApiKey`, never as a header.

        `events` is read back by the guide that owns this client, so the routing knob has
        one home: this client emits the retries under it, the guide emits the answers.
        """
        self.endpoint = endpoint
        # Declared, because pyright widens a `Literal` inferred from an assignment.
        self.events: Events = events
        self._retry = retry
        self._api_key = api_key
        self._http = httpx.AsyncClient(timeout=timeout.total_seconds(), follow_redirects=True)

    async def evaluate(self, request: Request) -> Response:
        """`POST /v1/systemone`, resending a `429` or a `529` up to the retry policy's limit."""
        url = self.endpoint.url(EVALUATE)
        body = request.model_dump_json(by_alias=True)
        for attempt in range(self._retry.max_retries + 1):
            with attempt_span(
                "POST", url, EVALUATE, self.endpoint.host, self.endpoint.port, attempt
            ) as span:
                try:
                    response = await self._http.post(
                        url, content=body, headers=_sending(self._api_key)
                    )
                except httpx.HTTPError as error:
                    raise _transport(span, error) from error
                record_status(span, response.status_code)
                outcome = step(
                    response.status_code,
                    response.text,
                    _retry_after(response.headers),
                    attempt,
                    self._retry,
                )
                match outcome:
                    case Response():
                        return outcome
                    case Retry(delay=delay):
                        fail_attempt(span, str(response.status_code))
                        retry_event(span, response.status_code, attempt + 1, delay, self.events)
                        await asyncio.sleep(delay.total_seconds())
                    case GuidemeError():
                        fail_attempt(span, _attempt_error(response.status_code, outcome))
                        raise outcome
        # Unreachable: the loop runs at least once and every arm returns or raises.
        raise RateLimitedError(None)

    async def models(self) -> list[ModelEntry]:
        """`GET /v1/models`. Not retried."""
        url = self.endpoint.url(MODELS)
        with attempt_span("GET", url, MODELS, self.endpoint.host, self.endpoint.port, 0) as span:
            try:
                response = await self._http.get(url, headers=_auth(self._api_key))
            except httpx.HTTPError as error:
                raise _transport(span, error) from error
            record_status(span, response.status_code)
            return _models(
                span, response.status_code, response.text, _retry_after(response.headers)
            )

    async def close(self) -> None:
        """Close the connection pool."""
        await self._http.aclose()
