"""The one error tree every public function raises.

`kind` is the cross-SDK name for the failure, the same string guideme-rust's
`Error::kind()` returns, and the value of `error.type` on a failed span. Group
metrics by it; it is low-cardinality and stable.
"""

from datetime import timedelta
from typing import ClassVar, Literal, final, override

type ErrorKind = Literal[
    "auth",
    "invalid",
    "rate_limited",
    "overloaded",
    "transport",
    "unexpected_status",
    "protocol",
    "unsure",
    "config",
]
"""Every value `GuidemeError.kind` can take. Part of the cross-SDK contract."""


class GuidemeError(Exception):
    """Base of every guideme error. Never raised directly."""

    kind: ClassVar[ErrorKind]

    def describe(self) -> str:
        """The span status description.

        Bodies that could echo the caller's state stay off the span, so the
        subclasses that carry one override this with a description that names
        the body instead of quoting it.
        """
        return str(self)


@final
class AuthError(GuidemeError):
    """`401`: missing or invalid API key. Never retried."""

    kind = "auth"

    def __init__(self) -> None:
        """Build the error; it carries no detail, because the API sends none."""
        super().__init__("unauthorized: missing or invalid TypeSafe API key")


@final
class InvalidError(GuidemeError):
    """`422`: the request failed validation. `detail` is the verbatim response body."""

    kind = "invalid"

    def __init__(self, detail: str) -> None:
        """Build the error around the response body, which names the offending field."""
        super().__init__(f"invalid request: {detail}")
        self.detail = detail

    @override
    def describe(self) -> str:
        return "invalid request: the 422 body is on the raised error"


@final
class RateLimitedError(GuidemeError):
    """`429` after every retry, or a `retry-after` longer than the cap."""

    kind = "rate_limited"

    def __init__(self, retry_after: timedelta | None) -> None:
        """Build the error around the last `retry-after` the API sent, if any."""
        super().__init__(f"rate limited (retry-after: {retry_after})")
        self.retry_after = retry_after


@final
class OverloadedError(GuidemeError):
    """`529` after every retry."""

    kind = "overloaded"

    def __init__(self) -> None:
        """Build the error; the API sends no detail with a 529."""
        super().__init__("TypeSafe is overloaded")


@final
class TransportError(GuidemeError):
    """Connection, TLS, timeout, or body-read failure. The cause is chained."""

    kind = "transport"

    def __init__(self, cause: Exception) -> None:
        """Build the error and chain `cause`, so a traceback keeps the original."""
        super().__init__(f"transport failure: {cause}")
        self.__cause__ = cause


@final
class UnexpectedStatusError(GuidemeError):
    """A status the API contract does not define."""

    kind = "unexpected_status"

    def __init__(self, status: int, body: str) -> None:
        """Build the error around the status and the verbatim response body."""
        super().__init__(f"unexpected status {status}: {body}")
        self.status = status
        self.body = body

    @override
    def describe(self) -> str:
        return f"unexpected status {self.status}: the body is on the raised error"


@final
class ProtocolError(GuidemeError):
    """The response violated the contract.

    Undecodable body, answer kind mismatch, unknown option or level, a
    probability outside `0..=1`, or a missing answer for a question asked.
    """

    kind = "protocol"

    def __init__(self, detail: str) -> None:
        """Build the error around what was violated."""
        super().__init__(f"protocol violation: {detail}")
        self.detail = detail


@final
class UnsureError(GuidemeError):
    """The policy labelled the answer unsure and nothing caught it."""

    kind = "unsure"

    def __init__(self, question: str, value: float, threshold: float) -> None:
        """Build the error around the question id, the judged value and the boundary."""
        super().__init__(
            f"unsure answer for question {question}: {value} against threshold {threshold}"
        )
        self.question = question
        self.value = value
        self.threshold = threshold


@final
class ConfigError(GuidemeError):
    """Bad configuration.

    Thresholds outside `0..=1`, `no_below > yes_above`, a missing or empty key,
    an empty batch, unserialisable state, or an empty or duplicated rubric.
    """

    kind = "config"

    def __init__(self, detail: str) -> None:
        """Build the error around what was wrong."""
        super().__init__(f"configuration error: {detail}")
        self.detail = detail
