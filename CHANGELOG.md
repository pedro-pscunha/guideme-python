# Changelog

## Unreleased

Nothing yet.

## 0.1.1 — 2026-09-21

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
  a blank or whitespace-only rubric or entry, a repeat within one clause, and an empty clause
  written out: `examples` and `counterexamples` default to `None`, so any empty sequence that
  arrives was typed on purpose and says nothing.
- Contradictory examples are a `ConfigError` too: one string as an example of two alternatives
  of the same question, or as both an example and a counterexample of the same alternative. One
  string as an example of one alternative and a counterexample of another stays legal — that is
  the confusable-options pattern the feature exists for.

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
