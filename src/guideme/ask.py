"""Shapes: a question, or a tuple, list or dict of shapes. The answer has the same shape.

One walk mints the ids `q0..qN` in encounter order and records each question's
settled thresholds; a second walk reads every outcome back into the shape the
caller handed in. Both are pure.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import cast, final

from guideme._json import Json
from guideme.errors import ConfigError, ProtocolError
from guideme.policy import Outcome, Policy, Thresholds
from guideme.question import Question, Spec

type Reply = Mapping[str, tuple[Outcome, Thresholds]]
"""Settled outcomes keyed by question id, as `Guide` assembles them from one response."""


@final
@dataclass(frozen=True, slots=True)
class Answered:
    """One question and the id it was given, kept so the outcome can be read back as its type."""

    qid: str
    question: Question[object]


type Claim = Answered | tuple[Claim, ...] | list[Claim] | dict[object, Claim]
"""The caller's shape with each question replaced by its claim on an answer."""


@final
@dataclass(slots=True)
class Plan:
    """Questions accumulated for one request, with each one's settled thresholds."""

    base: Policy
    specs: dict[str, tuple[Json, Spec]] = field(default_factory=dict[str, tuple[Json, Spec]])
    thresholds: dict[str, Thresholds] = field(default_factory=dict[str, Thresholds])

    def push(self, question: Question[object]) -> str:
        """Add one question, settle its thresholds against the guide's, and mint its id."""
        qid = f"q{len(self.specs)}"
        self.thresholds[qid] = question.policy.over(self.base).settle()
        self.specs[qid] = (question.instructions, question.spec)
        return qid


def encode(shape: object, plan: Plan) -> Claim:
    """Walk the shape in encounter order, minting ids. Anything else is a `ConfigError`."""
    match shape:
        case Question():
            # Only .policy, .instructions, .spec and .read() are used from here
            # on, and read() widens to object; no T is ever written back in.
            question = cast("Question[object]", shape)
            return Answered(plan.push(question), question)
        case tuple():
            return tuple(encode(item, plan) for item in cast("tuple[object, ...]", shape))
        case list():
            return [encode(item, plan) for item in cast("list[object]", shape)]
        case dict():
            items = cast("dict[object, object]", shape).items()
            return {key: encode(item, plan) for key, item in items}
        case _:
            # Over `object`, not over one of this package's own enums: this arm
            # is how anything that is not a shape gets rejected.
            detail = f"not a question shape: {type(shape).__name__}"
            raise ConfigError(detail)


def decode(claim: Claim, reply: Reply) -> object:
    """Read every outcome back into the shape the caller handed in."""
    match claim:
        case Answered(qid=qid, question=question):
            settled = reply.get(qid)
            if settled is None:
                detail = f"no answer for question {qid}"
                raise ProtocolError(detail)
            outcome, thresholds = settled
            return question.read(qid, outcome, thresholds)
        case tuple():
            return tuple(decode(item, reply) for item in claim)
        case list():
            return [decode(item, reply) for item in claim]
        case dict():
            return {key: decode(item, reply) for key, item in claim.items()}
