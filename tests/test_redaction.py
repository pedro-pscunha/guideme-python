import json
import pickle
from collections.abc import Callable
from dataclasses import dataclass
from typing import final

import pytest
from opentelemetry.trace import StatusCode
from pytest_httpserver import HTTPServer

from guideme import ApiKey, GuidemeError, InvalidError, UnexpectedStatusError
from guideme.question import noul
from guideme.telemetry import ASK_SPAN

from .conftest import TEST_KEY, TICKET, Recorded, Runner, attributes, expect_post

SENTINEL = "only-the-error-body-carries-this"
"""A marker nothing but the response body holds, so finding it on a span proves a leak."""


def test_api_key_never_leaks_through_repr_str_json_or_pickle() -> None:
    key = ApiKey("sk-live-0123456789")
    assert repr(key) == "ApiKey(***)"
    assert str(key) == "ApiKey(***)"
    assert "0123456789" not in f"{key!r}{key!s}{key}"
    with pytest.raises(TypeError):
        _ = json.dumps(key)
    with pytest.raises(TypeError):
        _ = pickle.dumps(key)


def _invalid_detail(error: GuidemeError) -> str:
    assert isinstance(error, InvalidError)
    return error.detail


def _unexpected_body(error: GuidemeError) -> str:
    assert isinstance(error, UnexpectedStatusError)
    return error.body


@final
@dataclass(frozen=True, slots=True)
class Echo:
    """One status whose body may quote the caller's state, and where that body may go."""

    status: int
    expected: type[GuidemeError]
    kind: str
    description: str
    carried: Callable[[GuidemeError], str]


ECHOES = [
    Echo(
        status=422,
        expected=InvalidError,
        kind="invalid",
        description="invalid request: the 422 body is on the raised error",
        carried=_invalid_detail,
    ),
    Echo(
        status=500,
        expected=UnexpectedStatusError,
        kind="unexpected_status",
        description="unexpected status 500: the body is on the raised error",
        carried=_unexpected_body,
    ),
]


@pytest.mark.parametrize("echo", ECHOES, ids=[case.kind for case in ECHOES])
def test_an_error_body_quoting_the_state_stays_on_the_error_and_off_every_span(
    httpserver: HTTPServer, runner: Runner, spans: Recorded, echo: Echo
) -> None:
    body = json.dumps({"detail": f"rejected: {TICKET}", "trace": SENTINEL})
    expect_post(httpserver).respond_with_data(body, status=echo.status)

    with pytest.raises(echo.expected) as raised:
        _ = runner.ask(noul("Urgent?"), TICKET)
    assert raised.value.kind == echo.kind
    assert echo.carried(raised.value) == body

    ask = spans.one(ASK_SPAN)
    assert attributes(ask)["error.type"] == echo.kind
    assert ask.status.status_code is StatusCode.ERROR
    assert ask.status.description == echo.description

    recorded = spans.texts()
    assert recorded
    leaks = (SENTINEL, TICKET, TEST_KEY)
    assert not [text for text in recorded if any(leak in text for leak in leaks)]

    described = runner.described()
    assert "ApiKey(***)" in described
    assert TEST_KEY not in described
