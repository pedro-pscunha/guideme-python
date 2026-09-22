"""What an ask cost and which model answered it, returned beside the answer.

Its own module rather than a name in `guideme.guide`, because the generated `ask`
surfaces name `Receipt` in their return types and `guideme.guide` imports those. One
module below them is where the type has to live for the graph to stay one-way.
"""

from dataclasses import dataclass
from typing import final

from guideme.api import Usage


@final
@dataclass(frozen=True, slots=True)
class Receipt[T]:
    """An answer with what the request cost and which model produced it.

    `Guide.ask` returns the answer alone, which is the shape almost every call wants.
    `ask_with_receipt` returns this instead, for cost attribution and for pinning a
    policy to the model version that produced the numbers it was tuned against.
    """

    answer: T
    """What `ask` would have returned: the caller's shape, answered."""

    model: str
    """The versioned model that answered, even where an alias such as `jev-latest` was asked
    for. Log it: thresholds are tuned against one model's numbers."""

    usage: Usage
    """Input and output tokens for the whole request. Input tokens are what is billed."""
