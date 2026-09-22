# Changelog

## Unreleased

### Documentation

- The README is rewritten in plain English with the headings, the order and the terms that
  every guideme SDK now shares. Design rationale and measurements it carried are in
  `docs/design.md`, and the contributor notes are in `CONTRIBUTING.md`. The TypeScript SDK is
  listed under **Other SDKs**. No behaviour changes.

## 0.2.0 — 2026-09-22

One breaking change, and it is one nobody outside this repository can have depended on yet.
Everything else is additive.

### Breaking

- `OverloadedError` now takes the `retry-after` the API sent: `OverloadedError(retry_after)`
  with a `retry_after` attribute, mirroring `RateLimitedError`. A `529` carries that header as
  often as a `429` does, and both SDKs were throwing it away on the one path where it is the
  only thing that says when to come back. Constructing the error by hand is the only code this
  moves; catching it is unchanged.

### Added

- `ask_with_receipt` on `Guide` and `AsyncGuide`, with the same overload family as `ask`. It
  returns `Receipt[T]` — `answer`, `model`, `usage` — so cost attribution and pinning a policy
  to the model version that produced its numbers no longer need an OpenTelemetry pipeline.
  `ask` is that call followed by `.answer`. `Receipt` and `Usage` are exported from `guideme`,
  and `Usage` is a frozen dataclass of two `int`s copied out of the wire model, so no
  pydantic type reaches the top-level surface — the trade `ModelInfo` already makes.
- `with` and `async with` on the two guides, each closing the guide on the way out. The pool a
  guide holds is now counted: `with_policy(…)` takes a second hold on it, and closing either
  guide leaves the other able to ask. Before this, closing a derived guide closed its parent's
  pool, which was a documented sharp edge and would have been a trap under `with`.
- `GuideBuilder.transport(…)` and `.async_transport(…)`, taking an `httpx.BaseTransport` and an
  `httpx.AsyncBaseTransport`. A proxy, a client certificate, or an `httpx.MockTransport` that
  answers a test with no server, no port and no key — the README's new **Testing your code**
  section is that test written out. A transport and `timeout(…)` refuse each other in either
  order, because a custom transport is free to ignore the budget `httpx` hands it and a
  silent no-op is worse than a `ConfigError`; so does building the wrong kind of guide from
  one, and so does setting both transports on one builder, which could build neither.
- `GET /v1/models` is retried on `429` and `529`, through the same loop and the same spans an
  ask uses. The API's docs say an SDK handles a `429` for you, and a `429` during startup used
  to fail the start.
- A failed connection is retried inside the same `max_retries` budget and backoff:
  `httpx.ConnectError`, which means the request never reached a server, so nothing was
  judged and nothing is repeated. A disconnect part-way through a response and a body that
  will not decode are still not retried — the request arrived, and a resend would buy the
  same judgment twice. **No timeout is retried, of any phase**, `httpx.ConnectTimeout`
  included: Rust sets one deadline over the whole attempt and cannot tell a connect timeout
  from a read one, so retrying it here would make the two SDKs disagree about the same
  failure, and a retried timeout multiplies the wall time `timeout(…)` exists to bound.
- `guideme.__all__` gains `Question`, `NoulQuestion`, `ChoiceQuestion`, `ScoreQuestion`,
  `DetailedNoul`, `DetailedChoice`, `DetailedScore`, `Receipt` and `Usage`, reaching 44 names.
  Annotating a stored question no longer means importing from a module the README calls
  private. `guideme.api` gains an `__all__` of its own, so `import *` from it stops handing
  back `BaseModel`, `Field` and `Mapping`; `guideme.question`, `guideme.policy`,
  `guideme.enums` and `guideme.errors` each gained one too.

### Fixed

- `with_policy(…)` no longer leaks a hold on the connection pool when the patch it is given
  cannot settle. Python evaluates arguments left to right, so the hold was taken before the
  patch was validated and nothing released it: the guide that would have was never built.
  The patch settles first now. A pool that never closes is invisible until a process runs
  out of sockets, so the regression asserts the count rather than the symptom.
- The pool's count and every holder's spent-flag are taken under one lock. `Guide`'s
  docstring says to share a guide across threads, so two threads closing two guides over one
  pool is a documented thing to do, and a flag read, a flag flip and a decrement are three
  steps that must not interleave. `ask` is untouched and takes no lock.
- Closing one guide twice no longer closes the connection pool under a guide derived from it
  with `with_policy(…)`. `share()` now hands back a distinct client over the shared pool, each
  carrying its own release-once flag, so a guide releases exactly once however many times it
  is closed; a count alone cannot tell which holder a release came from. Sharing from an
  already-closed guide is a `ConfigError` rather than a guide holding nothing.

### Changed

- `guideme.ask` walks a shape through four `TypeGuard` predicates instead of four `cast()`
  calls. There are now no casts anywhere in `src/guideme`: a `TypeGuard` replaces the narrowed
  type outright where an annotated assignment only intersects with it, so the element types
  are `object` rather than unknown and a checker verifies what was being asserted before.

- `guideme.retry` carries `error.type = "transport"` and **no** `http.response.status_code`
  when the attempt it is resending never got a response. Exactly one of the two is on every
  such event. A dashboard grouping retries by cause has to tell a throttled API from an
  unreachable one, so the cause is which field is present. `docs/observability.md` has the
  table and `docs/contract.md` the retry policy in full.

### Documented

- The timeout's scope, which is per phase in `httpx` and per attempt in Rust's `reqwest`, is
  now on `GuideBuilder.timeout`, in the README's configuration table and in `docs/contract.md`
  as a stated divergence rather than something a reader has to find.
- Concurrency: build one guide, share it across threads or tasks, close it once. It was true
  before and written down nowhere.
- `docs/contract.md` drops the rubric asymmetry between the SDKs' runtime constructors, which
  guideme-rust closed at 0.2.0, and corrects a stale `Rubric::into_wire()` to `Rubric::render`.

## 0.1.1 — 2026-09-22

Additive. Nothing that worked in 0.1.0 sends different bytes.

- `option(rubric, examples=…, counterexamples=…)` and `level(rubric, examples=…)` join
  `fallback(…)`, which now takes the same keywords. All three are exported from `guideme`,
  bringing `__all__` to 35 names. A rubric written as a bare string keeps working everywhere.
- The parts are composed into the rubric where it becomes wire text: clauses joined with a
  newline, items within one joined with `"; "`, in the order written, the text verbatim. A
  rubric with no examples renders to its own text, byte for byte, so an existing request is
  unchanged. The rendered string and its order are cross-SDK contract items, stated in
  `docs/contract.md` and pinned by `spec/vectors/rubric.json`.
- All three kinds of question take them. `noul("…").criteria(yes, no)` accepts an `option(…)`
  for either side: a yes and a no are as confusable as two options, and there is no fourth
  constructor for them.
- Every site that puts a rubric on the wire renders — `Choice.rubric()`, `Levels.levels()`,
  `choose_among`, `score_levels` and `NoulQuestion.criteria` — so examples cannot be silently
  dropped by reaching a runtime constructor.
- `level(…)` takes no counterexamples: "not this option" means nothing on an ordered scale. An
  `option(…)` carrying counterexamples written where a level belongs is a `ConfigError`, as is
  a blank or whitespace-only entry, a repeat within one clause, examples attached to a blank
  rubric, and an empty clause written out: `examples` and `counterexamples` default to `None`,
  so any empty sequence that arrives was typed on purpose and says nothing. A blank rubric
  carrying no examples is untouched — it means what it meant in 0.1.0.
- Contradictory examples are a `ConfigError` too: one string as an example of two alternatives
  of the same question, or as both an example and a counterexample of the same alternative. One
  string as an example of one alternative and a counterexample of another stays legal — that is
  the confusable-options pattern the feature exists for.
- A clause given as one string is refused rather than shredded. A `str` is a `Sequence[str]` of
  its own characters, so `examples="refund"` would have become six one-letter examples and no
  type checker would have said so; it is a `ConfigError` naming the mistake, the same way
  `score_levels` already refuses a scale given as one string.
- `fallback(…)` is refused by `choose_among`, `score_levels` and `noul(…).criteria(…)`. Those
  answer in a `Key`, a `Rank` and a `bool`, none of which has a member to fall back to, so the
  marking had nothing to act on and was being dropped in silence. Use `.otherwise(…)` on the
  question, which is what the `Levels` rule has always said.
- An example or counterexample containing `U+000A` or `U+000D` is refused. Items are joined
  onto one line, so a newline inside one would read as a clause the rubric never declared. The
  rubric text itself is unrestricted; only the entries are. `"; "` inside an entry stays legal,
  because it changes how many examples a reader sees rather than which clause they are in.
- A rubric built by `option(…)`, `level(…)` or `fallback(…)` can be copied and pickled again.
  Carrying the parts meant `__new__` took four arguments where `str` hands back one, so
  `copy.copy`, `copy.deepcopy` and `pickle` raised a `TypeError` — including on the
  `copy.deepcopy({"key": option(…)})` a caller writes before `choose_among`. A bare string did
  this in 0.1.0 and does it again.

## 0.1.0 — 2026-09-21

First release. Everything below is new, so this entry lists the surface rather than the
changes to it.

- `Guide` and `AsyncGuide`, built by `Guide.from_env()`, `AsyncGuide.from_env()` or the shared
  `GuideBuilder`, which takes `api_key`, `base_url`, `model`, `policy`, `max_retries`,
  `backoff`, `timeout`, `events` and `record_state` and ends in `.build()` or
  `.build_async()`. Both guides answer `ask`, `models`, `with_policy` and `close`.
- One verb: `ask(shape, state)`. `models()` returns `tuple[ModelInfo, ...]`, one entry per
  model the account may use, with its name, description and release date.
- Questions as values: `noul`, `choose`, `score`, `choose_among`, `score_levels`, with
  `.yes_above`, `.no_below`, `.min_confidence`, `.criteria`, `.otherwise` and `.detail`.
  A `Choice` takes 1 to 255 options and a `Levels` 2 to 10, checked where the rubric is
  written.
- `Choice` and `Levels` enum bases: a member's name is its wire key and its value is its
  rubric, `fallback(...)` marks the member to use when the policy says unsure, and both
  validate where the enum is written. Two members may not share a rubric, because Python
  would make the second an alias of the first, and `fallback(...)` marks a `Choice` member
  only; a score falls back through `.otherwise(level)` on the question.
- Pure `policy.resolve` with `Policy` patches and validated `Thresholds`; the unsure ladder is
  `.otherwise(value)`, then the rubric's fallback member, then `UnsureError`.
- Shapes: a question, or a tuple, list or dict of shapes, answered in one request with ids
  `q0..qN` in encounter order. The typed forms are generated overloads, so the synchronous and
  asynchronous surfaces cannot drift.
- Telemetry on two signals: one `guideme.ask` span per request with an HTTP client span per
  attempt, and every answer and retry as both a span event and an OTLP log record, at `INFO`
  and at `WARN`, carrying the trace and span ids of the span they came from. `events("span")`
  or `events("log")` on the builder stores each one once where both pipelines run; `"both"` is
  the default and costs nothing without a logger provider. No record is ever emitted at
  `ERROR`: a failure is raised and marked on the span. The state is recorded on the ask span
  only under `record_state(True)`; its length always is. The logs API is private in
  `opentelemetry-api`, so it is resolved defensively: a release without it costs the logs
  signal and nothing else, and `events("log")` or `events("both")` is then a `ConfigError`
  where the guide is configured rather than records that silently go nowhere. That holds for
  a release that renames a severity or changes what a log record takes, not only for one that
  removes the module: everything guideme reads from the private API is resolved once, behind
  the one guard. A sink that raises is swallowed, so a logging failure never reaches the
  caller or the ask span; a record guideme cannot build is not a sink failure and is not
  swallowed.
- One error tree under `GuidemeError`, each class carrying the `.kind` string every guideme
  SDK reports: `AuthError`, `InvalidError`, `RateLimitedError`, `OverloadedError`,
  `TransportError`, `UnexpectedStatusError`, `ProtocolError`, `UnsureError` and `ConfigError`.
  A `ConfigError` is raised where the mistake is written, before a socket is opened.
- `ApiKey`, which carries the key and never prints it: `repr` and `str` are both `ApiKey(***)`,
  it has no serialisation and refuses to pickle, and it is scrubbed from anything the package
  reports.
  `Model` is validated on construction; `Probability`, `Confidence`, `Key` and `Rank` are
  `NewType` brands that the wire mints and validates, so the guarantee is where they come
  from rather than a check at every call.
- A second supported tier, imported by its own path: `guideme.api`, the wire mirror of
  `POST /v1/systemone` and `GET /v1/models`, with `Client` and `AsyncClient` in
  `guideme.api.client`; `guideme.policy`, holding `resolve`, the pure decision function; and
  the single name `guideme.question.Question`, the type every question constructor returns,
  for annotating a question you store or pass on. The rest of `guideme.question` is private.
- `GuideBuilder.from_env()` applies `TYPESAFE_API_KEY`, `TYPESAFE_BASE_URL` and
  `GUIDEME_MODEL` onto a builder, so a setting with no environment variable can be chained
  after them; `Guide.from_env()` and `AsyncGuide.from_env()` are that step plus `build()`.
- `spec/`: the JSON Schemas and the 42 golden policy vectors vendored from `guideme-rust`, run
  as the conformance suite, with a CI job that fails when this copy drifts from that
  repository's `main`.
