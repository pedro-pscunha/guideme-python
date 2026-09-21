import pytest

from guideme import ProtocolError, Thresholds
from guideme.policy import resolve

from .conftest import (
    REPO_ROOT,
    Json,
    answer_from_json,
    as_float,
    as_list,
    as_object,
    as_str,
    load_json,
    outcome_json,
)

VECTORS = as_list(load_json(REPO_ROOT / "spec" / "vectors" / "policy.json"))


@pytest.mark.parametrize("raw", VECTORS, ids=[f"v{i}" for i in range(len(VECTORS))])
def test_every_golden_vector_resolves_exactly_as_the_contract_says(raw: Json) -> None:
    entry = as_object(raw)
    thresholds = Thresholds(**{k: as_float(v) for k, v in as_object(entry["thresholds"]).items()})
    answer = answer_from_json(entry["answer"])
    if "error" in entry:
        assert as_str(entry["error"]) == "protocol"
        with pytest.raises(ProtocolError):
            _ = resolve(answer, thresholds)
    else:
        assert outcome_json(resolve(answer, thresholds)) == entry["outcome"]
