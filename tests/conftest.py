import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import cast, final

import pytest
from hypothesis import HealthCheck, settings
from jsonschema import Draft202012Validator
from pydantic import TypeAdapter
from pytest_httpserver import HTTPServer

from guideme import ApiKey, AsyncGuide, Guide, GuideBuilder
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


TEST_KEY = "unit-test-key-not-a-secret"
"""What the local server is told to expect. Nothing real; the live tests read the real key."""

SYNC = "sync"
ASYNC = "async"

type Configure = Callable[[GuideBuilder], GuideBuilder]
"""A test's extra builder settings, applied after the ones every test shares."""


def _entry(guide: Guide) -> Callable[[object, Json], object]:
    """`Guide.ask`, widened to the signature its implementation actually has.

    `ask` is a stack of overloads over concrete shapes, and this fixture is
    shape-agnostic on purpose, so it cannot name one. The call still goes through the
    public method; `tests/typing/` is what proves the overloads carry the right types.
    """
    return cast("Callable[[object, Json], object]", guide.ask)


def _async_entry(guide: AsyncGuide) -> Callable[[object, Json], Awaitable[object]]:
    """`AsyncGuide.ask`, widened for the same reason as `_entry`."""
    return cast("Callable[[object, Json], Awaitable[object]]", guide.ask)


@final
@dataclass(frozen=True, slots=True)
class Runner:
    """One ask against the local server, run once as `Guide` and once as `AsyncGuide`.

    Both kinds build, ask and close inside the same call, so an `AsyncGuide`'s pool is
    never left open across event loops.
    """

    kind: str
    base_url: str

    def _builder(self, configure: Configure | None) -> GuideBuilder:
        builder = (
            GuideBuilder()
            .api_key(ApiKey(TEST_KEY))
            .base_url(self.base_url)
            .backoff(timedelta(milliseconds=10))
        )
        return builder if configure is None else configure(builder)

    def ask(self, shape: object, state: Json, configure: Configure | None = None) -> object:
        """Ask `shape` about `state` and return what the caller's shape reads back as."""
        if self.kind == SYNC:
            guide = self._builder(configure).build()
            try:
                return _entry(guide)(shape, state)
            finally:
                guide.close()
        return asyncio.run(self._ask_async(shape, state, configure))

    async def _ask_async(self, shape: object, state: Json, configure: Configure | None) -> object:
        guide = self._builder(configure).build_async()
        try:
            return await _async_entry(guide)(shape, state)
        finally:
            await guide.close()

    def described(self, configure: Configure | None = None) -> str:
        """`repr` of a guide of this kind, without asking anything."""
        if self.kind == SYNC:
            guide = self._builder(configure).build()
            try:
                return repr(guide)
            finally:
                guide.close()
        return asyncio.run(self._described_async(configure))

    async def _described_async(self, configure: Configure | None) -> str:
        guide = self._builder(configure).build_async()
        try:
            return repr(guide)
        finally:
            await guide.close()


@pytest.fixture(params=[SYNC, ASYNC])
def runner(request: pytest.FixtureRequest, httpserver: HTTPServer) -> Runner:
    """Every wire and tracing assertion runs twice, once per kind, from this one fixture."""
    return Runner(kind=str(request.param), base_url=httpserver.url_for(""))
