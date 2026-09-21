import json
import pickle

import pytest
from pytest_httpserver import HTTPServer

from guideme import ApiKey, ConfigError
from guideme.question import noul

from .conftest import TEST_KEY, Recorded, Runner, attributes
from .test_wire import JSON, TICKET, expect_post, noul_reply


def test_api_key_never_leaks_through_repr_str_json_or_pickle() -> None:
    key = ApiKey("sk-live-0123456789")
    assert repr(key) == "ApiKey(***)"
    assert str(key) == "ApiKey(***)"
    assert "0123456789" not in f"{key!r}{key!s}{key}"
    with pytest.raises(TypeError):
        _ = json.dumps(key)
    with pytest.raises(TypeError):
        _ = pickle.dumps(key)
    with pytest.raises(ConfigError):
        _ = ApiKey("")


def test_the_key_reaches_no_span_attribute_and_the_guide_repr_redacts(
    httpserver: HTTPServer, runner: Runner, spans: Recorded
) -> None:
    expect_post(httpserver).respond_with_data(noul_reply(0.95), content_type=JSON)
    assert runner.ask(noul("Urgent?"), TICKET, lambda builder: builder.record_state(True)) is True

    recorded = [
        str(value)
        for span in spans.all()
        for carrier in (span, *span.events)
        for value in attributes(carrier).values()
    ]
    assert recorded
    assert not [value for value in recorded if TEST_KEY in value]
    assert not [span.status.description for span in spans.all() if span.status.description]

    described = runner.described()
    assert "ApiKey(***)" in described
    assert TEST_KEY not in described
