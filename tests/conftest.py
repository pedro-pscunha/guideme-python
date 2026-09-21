import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

from hypothesis import HealthCheck, settings
from jsonschema import Draft202012Validator
from pydantic import TypeAdapter

from guideme._json import Json
from guideme.api import Answer as WireAnswer
from guideme.api import answer_from_wire
from guideme.policy import (
    Answer,
    ChoiceOutcome,
    NoulOutcome,
    Outcome,
    ScoreOutcome,
)

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


def outcome_json(outcome: Outcome) -> Json:
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
