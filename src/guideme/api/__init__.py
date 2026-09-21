"""The wire: a typed mirror of `POST /v1/systemone` and `GET /v1/models`.

Also the adapters between that mirror and the pure core.

No policy is decided here; that is `guideme.policy`. This package is the only importer
of `pydantic`, and `guideme.api.client` beneath it the only importer of `httpx`, so
neither library reaches a type a caller can name.

The shapes are pinned by `spec/schema/request.json` and `spec/schema/response.json`.
What guideme sends refuses an unknown field, because an extra key there would be this
package's own bug; what guideme parses ignores one, because the API may grow a field
before this package knows about it.
"""

from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    ValidationError,
    model_serializer,
)

from guideme._json import Json
from guideme.errors import ConfigError, ProtocolError
from guideme.policy import Answer as JudgedAnswer
from guideme.policy import ChoiceAnswer as JudgedChoice
from guideme.policy import NoulAnswer as JudgedNoul
from guideme.policy import ScoreAnswer as JudgedScore
from guideme.question import ChoiceSpec, NoulSpec, ScoreSpec, Spec
from guideme.scalars import confidence, probability


class _Sent(BaseModel):
    """Base of everything guideme puts on the wire.

    `extra="forbid"` because guideme builds these: an unexpected field is a bug here,
    not a message from the API. `allow_inf_nan=False` because pydantic's default is to
    serialise a `NaN` or an infinity as `null`, which would turn a caller's broken
    number into a field the API reads as absent; refused here instead. The serialiser
    setting is the belt to that brace: nothing can reach it, and if something did it
    would put `NaN` on the wire and be rejected rather than silently dropped.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        ser_json_inf_nan="constants",
    )


class _Received(BaseModel):
    """Base of everything guideme parses.

    `extra="ignore"` because the API may add a field before this package knows it, and
    neither `spec/schema/response.json` nor the Rust reference rejects one.
    """

    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)


# The models below are deliberately not `@final`: pyright checks a final class for
# instance variables its bases declare but never assign, and `BaseModel` declares a
# dozen of those for pydantic's own machinery. Frozen models with a closed union are
# what keeps them from being extended in practice.


type Unit = Annotated[float, Field(ge=0.0, le=1.0)]
"""A probability or a confidence as it arrives: outside `0..=1` it fails at parse time."""


type Count = Annotated[int, Field(ge=0)]
"""A token count as it arrives. `spec/schema/response.json` sets `minimum: 0` and the Rust
mirror reads a `u64`, so a negative is a malformed body, refused at parse time rather than
carried onto `gen_ai.usage.*`."""


class NoulCriteria(_Sent):
    """What a yes and a no mean.

    The wire names these two fields `true` and `false`, and so does this mirror. An
    alias would let the Python names be `yes` and `no`, but the synthesised `__init__`
    a type checker sees takes the alias anyway, so the alias would buy nothing and hide
    which name goes on the wire. `guideme.question.NoulCriteria` is the `yes`/`no` one.
    """

    true: str
    false: str


class NoulQuestion(_Sent):
    """A yes/no question on the wire."""

    type: Literal["noul"] = "noul"
    instructions: Json
    criteria: NoulCriteria | None = None

    @model_serializer(mode="wrap")
    def _drop_absent_criteria(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        """Omit `criteria` when there is none, rather than sending an explicit null.

        The schema permits both, and the Rust mirror omits it. `exclude_none` on the
        dump would do the same thing but would also drop a `state` or an `instructions`
        the caller meant to be null, and both of those are required fields.
        """
        fields: dict[str, object] = handler(self)
        if self.criteria is None:
            _ = fields.pop("criteria", None)
        return fields


class ChoiceQuestion(_Sent):
    """A choice on the wire: each option key mapped to its rubric, or to null."""

    type: Literal["choice"] = "choice"
    instructions: Json
    criteria: dict[str, str | None]


class ScoreQuestion(_Sent):
    """A score on the wire: the level descriptions, low to high."""

    type: Literal["score"] = "score"
    instructions: Json
    criteria: list[str]


type Question = Annotated[
    NoulQuestion | ChoiceQuestion | ScoreQuestion, Field(discriminator="type")
]
"""One question on the wire. The `type` tag selects the variant."""


class Request(_Sent):
    """The `POST /v1/systemone` body."""

    state: Json
    model: str
    questions: dict[str, Question]


class NoulAnswer(_Received):
    """The probability of yes."""

    type: Literal["noul"]
    noul: Unit


class ChoiceAnswer(_Received):
    """The chosen option with the whole distribution."""

    type: Literal["choice"]
    choice: str
    probabilities: dict[str, Unit]
    confidence: Unit


class ScoreAnswer(_Received):
    """A weighted position on the levels; `legend` and `probabilities` are keyed by index."""

    type: Literal["score"]
    score: float
    legend: dict[str, str]
    probabilities: dict[str, Unit]
    confidence: Unit


type Answer = Annotated[NoulAnswer | ChoiceAnswer | ScoreAnswer, Field(discriminator="type")]
"""One answer on the wire. The `type` tag matches the question's."""


class Usage(_Received):
    """Token usage for one request. Input tokens are billed; output tokens are free."""

    input_tokens: Count
    output_tokens: Count


class Response(_Received):
    """The `POST /v1/systemone` body that comes back."""

    model: str
    answers: dict[str, Answer]
    usage: Usage


class ModelEntry(_Received):
    """One entry of `GET /v1/models`, as the API describes it."""

    name: str
    description: str
    release_date: str


class ModelsResponse(_Received):
    """The `GET /v1/models` body."""

    models: list[ModelEntry]


def question_to_wire(instructions: Json, spec: Spec) -> Question:
    """Put one question's spec on the wire. The spec was validated where it was written.

    Only the instructions are the caller's, so a value that is not JSON-shaped is their
    configuration error rather than a `pydantic.ValidationError` escaping this package.
    """
    try:
        return _question(instructions, spec)
    except ValidationError as error:
        detail = f"instructions are not JSON-shaped: {validation_detail(error)}"
        raise ConfigError(detail) from error


def _question(instructions: Json, spec: Spec) -> Question:
    match spec:
        case NoulSpec(criteria=criteria):
            wire = None if criteria is None else NoulCriteria(true=criteria.yes, false=criteria.no)
            return NoulQuestion(instructions=instructions, criteria=wire)
        case ChoiceSpec(rubric=rubric):
            return ChoiceQuestion(instructions=instructions, criteria=dict(rubric))
        case ScoreSpec(levels=levels):
            return ScoreQuestion(instructions=instructions, criteria=list(levels))


def validation_detail(error: ValidationError) -> str:
    """One compact line from a pydantic failure, with the offending value left out.

    That value is either the caller's state or a response body, and this text becomes a
    span's status description, where neither belongs. Pydantic's own message quotes the
    input and appends a documentation URL.
    """
    first = error.errors(include_url=False, include_input=False)[0]
    where = ".".join(str(part) for part in first["loc"]) or "(root)"
    return f"{where}: {first['msg']} ({error.error_count()} errors)"


def request_to_wire(state: Json, model: str, questions: Mapping[str, Question]) -> Request:
    """Build the request body.

    Every part but the state was built by this package. The state is the caller's, so a
    value that is not JSON-shaped is reported as their configuration error.
    """
    try:
        return Request(state=state, model=model, questions=dict(questions))
    except ValidationError as error:
        detail = f"state is not JSON-shaped: {validation_detail(error)}"
        raise ConfigError(detail) from error


def _level_keys[T](raw: Mapping[str, T], what: str) -> dict[int, T]:
    """Read a map keyed by level index. The wire spells those indices as decimal strings."""
    keyed: dict[int, T] = {}
    for key, value in raw.items():
        if not (key.isascii() and key.isdecimal()):
            detail = f"{what} key {key!r} is not a level index"
            raise ProtocolError(detail)
        keyed[int(key)] = value
    if len(keyed) != len(raw):
        detail = f"{what} spells one level index two ways"
        raise ProtocolError(detail)
    return keyed


def answer_from_wire(answer: Answer) -> JudgedAnswer:
    """Wire answer to the core's answer: scalars parsed once, level keys read as indices."""
    match answer:
        case NoulAnswer(noul=noul):
            return JudgedNoul(probability(noul))
        case ChoiceAnswer(choice=choice, probabilities=probabilities, confidence=reported):
            return JudgedChoice(
                choice=choice,
                probabilities={key: probability(p) for key, p in probabilities.items()},
                confidence=confidence(reported),
            )
        case ScoreAnswer(
            score=score, legend=legend, probabilities=probabilities, confidence=reported
        ):
            return JudgedScore(
                score=score,
                legend=_level_keys(legend, "legend"),
                probabilities={
                    index: probability(p)
                    for index, p in _level_keys(probabilities, "probabilities").items()
                },
                confidence=confidence(reported),
            )
