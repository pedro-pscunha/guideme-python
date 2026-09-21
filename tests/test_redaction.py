import json
import pickle

import pytest

from guideme import ApiKey, ConfigError


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
