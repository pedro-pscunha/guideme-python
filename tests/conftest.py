import json
from pathlib import Path
from typing import cast

from hypothesis import HealthCheck, settings

from guideme._json import Json
from guideme.policy import (
    Answer,
    ChoiceAnswer,
    ChoiceOutcome,
    NoulAnswer,
    NoulOutcome,
    Outcome,
    ScoreAnswer,
    ScoreOutcome,
)
from guideme.scalars import confidence, probability

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


# Lane B replaces this with guideme.api.answer_from_wire.
def answer_from_json(raw: Json) -> Answer:
    entry = as_object(raw)
    match as_str(entry["type"]):
        case "noul":
            return NoulAnswer(probability(as_float(entry["noul"])))
        case "choice":
            probabilities = as_object(entry["probabilities"])
            return ChoiceAnswer(
                choice=as_str(entry["choice"]),
                probabilities={k: probability(as_float(v)) for k, v in probabilities.items()},
                confidence=confidence(as_float(entry["confidence"])),
            )
        case "score":
            legend = as_object(entry["legend"])
            probabilities = as_object(entry["probabilities"])
            return ScoreAnswer(
                score=as_float(entry["score"]),
                legend={int(k): as_str(v) for k, v in legend.items()},
                probabilities={int(k): probability(as_float(v)) for k, v in probabilities.items()},
                confidence=confidence(as_float(entry["confidence"])),
            )
        case other:
            raise AssertionError(other)


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
