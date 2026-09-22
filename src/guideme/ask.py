"""Shapes: a question, or a tuple, list or dict of shapes. The answer has the same shape.

One walk mints the ids `q0..qN` in encounter order and records each question's
settled thresholds; a second walk reads every outcome back into the shape the
caller handed in. Both are pure.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TypeGuard, final

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


# The four predicates below are what lets `encode` take `object` and still type-check
# without a `cast`. A bare `isinstance` narrows `object` to `tuple[Unknown, ...]`, and
# strict's `reportUnknown*` family then fires on every use of the elements; annotating the
# result does not help, because narrowing intersects with the declaration rather than
# replacing it. A `TypeGuard` replaces it outright, so the element type is `object`, which
# is exactly what `encode` accepts and therefore claims nothing the isinstance has not
# already proved. `TypeIs` would be the tighter spelling and needs 3.12 to mean this;
# `TypeGuard` has meant it since 3.10, which is under the floor this package supports.


def _is_question(value: object) -> TypeGuard[Question[object]]:
    """A question of anything.

    Only `.policy`, `.instructions`, `.spec` and `.read()` are used from here on, and
    `read()` widens to `object`, so no `T` is ever written back in.
    """
    return isinstance(value, Question)


def _is_tuple(value: object) -> TypeGuard[tuple[object, ...]]:
    """A tuple of anything. The element type is `object`, which is what `encode` takes."""
    return isinstance(value, tuple)


def _is_list(value: object) -> TypeGuard[list[object]]:
    """A list of anything, for the same reason as `_is_tuple`."""
    return isinstance(value, list)


def _is_dict(value: object) -> TypeGuard[dict[object, object]]:
    """A dict of anything, for the same reason as `_is_tuple`."""
    return isinstance(value, dict)


def encode(shape: object, plan: Plan) -> Claim:
    """Walk the shape in encounter order, minting ids. Anything else is a `ConfigError`.

    An if-chain rather than a `match`, because the narrowing is what the predicates above
    are for and a class pattern cannot call one. The fall-through is how anything that is
    not a shape gets rejected, and it is over `object` rather than one of this package's
    own unions, so it is not the catch-all arm the invariants forbid.
    """
    if _is_question(shape):
        return Answered(plan.push(shape), shape)
    if _is_tuple(shape):
        return tuple(encode(item, plan) for item in shape)
    if _is_list(shape):
        return [encode(item, plan) for item in shape]
    if _is_dict(shape):
        return {key: encode(item, plan) for key, item in shape.items()}
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
