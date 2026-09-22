"""What happens when a request fails, is resent, or goes through a caller's transport.

`test_wire.py` holds the checks that say what guideme puts on the wire and reads back.
This module holds the ones about a request that does not simply succeed: the status-to-error
table, the retry policy on both endpoints, what is and is not resent before a response
arrives, an injected transport, and the connection pool two guides share. They are one
subject because they are one code path — `Client._fetch` and the pure `step` beneath it —
and every one of them needs a server that misbehaves on purpose.
"""

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import final

import httpx
import pytest
from pytest_httpserver import HTTPServer

from guideme import (
    AuthError,
    GuideBuilder,
    GuidemeError,
    InvalidError,
    OverloadedError,
    RateLimitedError,
    TransportError,
    UnexpectedStatusError,
)
from guideme.api.client import EVALUATE, MODELS
from guideme.question import noul
from guideme.telemetry import ASK_SPAN, RETRY_EVENT

from .conftest import (
    JSON,
    MODELS_BODY,
    TICKET,
    Configure,
    Handler,
    Recorded,
    Runner,
    answering_offline,
    as_list,
    as_object,
    async_entry,
    attributes,
    closed_port,
    configured,
    expect_post,
    noul_reply,
)

RETRIES = 1
"""Retries each failing-status case allows, so an exhausted one is exactly two requests."""

DETAIL = '{"detail":"questions.q0.criteria: must not be empty"}'
"""A 422 body, which the error must carry verbatim and the span must not."""

type Serve = Callable[[HTTPServer], Configure | None]
"""How one failure case arranges the server, returning any builder change it needs."""

type Check = Callable[[GuidemeError], None]
"""What one failure case asserts about the raised error beyond its class and kind."""


def _status(status: int, body: str = "", retry_after: str | None = None) -> Serve:
    headers = None if retry_after is None else {"retry-after": retry_after}

    def serve(httpserver: HTTPServer) -> Configure | None:
        expect_post(httpserver).respond_with_data(body, status=status, headers=headers)

    return serve


def _refused(_httpserver: HTTPServer) -> Configure | None:
    return lambda builder: builder.base_url(f"http://127.0.0.1:{closed_port()}")


def _nothing_more(_error: GuidemeError) -> None:
    """The class and the kind are the whole contract for this status."""


def _carries_the_body(error: GuidemeError) -> None:
    assert isinstance(error, InvalidError)
    assert error.detail == DETAIL


def _parsed_the_retry_after(error: GuidemeError) -> None:
    assert isinstance(error, RateLimitedError)
    assert error.retry_after == timedelta(seconds=0)


def _kept_the_retry_after(error: GuidemeError) -> None:
    assert isinstance(error, OverloadedError)
    assert error.retry_after == timedelta(seconds=0)


@final
@dataclass(frozen=True, slots=True)
class Failure:
    """One failing call: how the server behaves, and everything the caller must see."""

    serve: Serve
    expected: type[GuidemeError]
    kind: str
    served: int
    check: Check


FAILURES = [
    Failure(_status(401), AuthError, "auth", 1, _nothing_more),
    Failure(_status(422, body=DETAIL), InvalidError, "invalid", 1, _carries_the_body),
    Failure(
        _status(429, retry_after="0"),
        RateLimitedError,
        "rate_limited",
        RETRIES + 1,
        _parsed_the_retry_after,
    ),
    Failure(
        _status(500, body="upstream exploded"),
        UnexpectedStatusError,
        "unexpected_status",
        1,
        _nothing_more,
    ),
    Failure(
        _status(529, retry_after="0"),
        OverloadedError,
        "overloaded",
        RETRIES + 1,
        _kept_the_retry_after,
    ),
    Failure(_refused, TransportError, "transport", 0, _nothing_more),
]


@pytest.mark.parametrize("failure", FAILURES, ids=[case.kind for case in FAILURES])
def test_every_failure_raises_its_typed_error_and_marks_the_ask_span(
    httpserver: HTTPServer, runner: Runner, spans: Recorded, failure: Failure
) -> None:
    extra = failure.serve(httpserver)

    def configure(builder: GuideBuilder) -> GuideBuilder:
        settled = builder.max_retries(RETRIES)
        return settled if extra is None else extra(settled)

    with pytest.raises(failure.expected) as raised:
        _ = runner.ask(noul("Urgent?"), TICKET, configure)
    assert raised.value.kind == failure.kind
    failure.check(raised.value)
    assert len(httpserver.log) == failure.served
    assert attributes(spans.one(ASK_SPAN))["error.type"] == failure.kind


BACKOFF = timedelta(milliseconds=300)
"""Long enough to measure that a retry really waited, short enough to pay for twice."""


def _waiting(builder: GuideBuilder) -> GuideBuilder:
    """Long enough a backoff that a resend cannot be mistaken for a fast first answer."""
    return builder.backoff(BACKOFF)


def _throttled_ask(httpserver: HTTPServer, runner: Runner) -> None:
    httpserver.expect_oneshot_request(EVALUATE, method="POST").respond_with_data("", status=429)
    expect_post(httpserver).respond_with_data(noul_reply(0.95), content_type=JSON)
    assert runner.ask(noul("Urgent?"), TICKET, _waiting) is True


def _throttled_models(httpserver: HTTPServer, runner: Runner) -> None:
    httpserver.expect_oneshot_request(MODELS, method="GET").respond_with_data("", status=429)
    httpserver.expect_request(MODELS, method="GET").respond_with_data(
        json.dumps(MODELS_BODY), content_type=JSON
    )
    assert len(runner.models(_waiting)) == len(as_list(as_object(MODELS_BODY)["models"]))


THROTTLED = [_throttled_ask, _throttled_models]
"""Both endpoints, each throttled once. The API docs promise a retry on either."""


@pytest.mark.parametrize("throttled", THROTTLED, ids=["evaluate", "models"])
def test_a_429_is_retried_after_waiting_out_the_backoff(
    httpserver: HTTPServer, runner: Runner, throttled: Callable[[HTTPServer, Runner], None]
) -> None:
    started = time.monotonic()
    throttled(httpserver, runner)
    assert time.monotonic() - started >= BACKOFF.total_seconds()
    assert len(httpserver.log) == 2


CONCURRENT = 2
"""Asks issued at once, which must wait out their retries together rather than in turn."""

ADVERTISED = 1.0
"""Seconds the server puts in `retry-after`; whole seconds are all the header expresses."""

MARGIN = 0.2
"""Slack below the sequential time, so the assertion fails on serialisation, not on load."""


async def _two_asks(base_url: str) -> list[object]:
    guide = configured(base_url).build_async()
    try:
        entry = async_entry(guide)
        return list(
            await asyncio.gather(entry(noul("Urgent?"), TICKET), entry(noul("Urgent?"), TICKET))
        )
    finally:
        await guide.close()


def test_two_async_asks_wait_out_their_retries_at_the_same_time(httpserver: HTTPServer) -> None:
    for _ in range(CONCURRENT):
        httpserver.expect_oneshot_request(EVALUATE, method="POST").respond_with_data(
            "", status=429, headers={"retry-after": "1"}
        )
    expect_post(httpserver).respond_with_data(noul_reply(0.95), content_type=JSON)

    started = time.monotonic()
    assert asyncio.run(_two_asks(httpserver.url_for(""))) == [True, True]
    elapsed = time.monotonic() - started

    assert elapsed >= ADVERTISED
    assert elapsed < CONCURRENT * ADVERTISED - MARGIN
    assert len(httpserver.log) == 2 * CONCURRENT


CLOSES = [1, 2]
"""How many times the derived guide is closed. Once is the ordinary case; twice is the
caller's mistake, and it must release once rather than spend the parent's hold as well."""


@pytest.mark.parametrize("closes", CLOSES, ids=["closed_once", "closed_twice"])
def test_neither_guide_closes_the_pool_while_the_other_still_holds_it(
    httpserver: HTTPServer, runner: Runner, closes: int
) -> None:
    expect_post(httpserver).respond_with_data(noul_reply(0.95), content_type=JSON)
    answers, held = runner.paired(noul("Urgent?"), TICKET, closes)
    assert answers == [True, True, True]
    assert len(httpserver.log) == 3
    assert not held


def test_an_injected_transport_answers_an_ask_with_no_server(runner: Runner) -> None:
    assert runner.offline(noul("Urgent?"), TICKET, answering_offline) is True


def _failing(failure: type[httpx.RequestError], detail: str) -> Handler:
    """A handler that raises the way one case asks instead of answering."""

    def fail(request: httpx.Request) -> httpx.Response:
        raise failure(detail, request=request)

    return fail


@final
@dataclass(frozen=True, slots=True)
class BeforeAResponse:
    """A failure that arrives with no response at all, and how guideme must treat it."""

    fail: Handler
    """What the transport raises. Each case raises it once and answers after that."""

    attempts: int
    """Calls the transport sees: one when the failure ends the ask, two when it is resent."""

    answered: bool
    """Whether an answer comes back, which only a resent failure can produce."""


NEVER_REACHED = [
    BeforeAResponse(_failing(httpx.ConnectError, "connection refused"), 2, answered=True),
    BeforeAResponse(_failing(httpx.ConnectTimeout, "connect timed out"), 1, answered=False),
    BeforeAResponse(_failing(httpx.ReadTimeout, "read timed out"), 1, answered=False),
    BeforeAResponse(_failing(httpx.PoolTimeout, "waited for a connection"), 1, answered=False),
    BeforeAResponse(_failing(httpx.RemoteProtocolError, "server hung up"), 1, answered=False),
]
"""One case that is resent and four that are not. A refused connection never reached a
server, so nothing was judged; everything else here either did arrive, or is a timeout, and
no timeout is resent whatever phase it names. `client.resend_after` carries the reasoning."""


@final
@dataclass(slots=True)
class _Flaky:
    """A transport that fails its first call the way one case asks, then answers."""

    fail: Handler
    calls: int = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.calls == 1:
            return self.fail(request)
        return answering_offline(request)


@pytest.mark.parametrize(
    "case",
    NEVER_REACHED,
    ids=[
        "connect_error",
        "connect_timeout",
        "read_timeout",
        "pool_timeout",
        "remote_protocol_error",
    ],
)
def test_only_a_failed_connection_is_resent_and_no_timeout_ever_is(
    runner: Runner, spans: Recorded, case: BeforeAResponse
) -> None:
    flaky = _Flaky(case.fail)
    if case.answered:
        assert runner.offline(noul("Urgent?"), TICKET, flaky) is True
    else:
        with pytest.raises(TransportError):
            _ = runner.offline(noul("Urgent?"), TICKET, flaky)
    assert flaky.calls == case.attempts

    resends = [
        event
        for span in spans.named(f"POST {EVALUATE}")
        for event in spans.events(span, RETRY_EVENT)
    ]
    assert len(resends) == case.attempts - 1
    for event in resends:
        carried = attributes(event)
        assert carried["error.type"] == "transport"
        assert "http.response.status_code" not in carried
