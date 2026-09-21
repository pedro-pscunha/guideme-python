# Changelog

## Unreleased

Nothing yet.

## 0.1.0 — 2026-09-21

First release.

- Questions as values: `noul`, `choose`, `score`, `choose_among`, `score_levels`, with
  `.yes_above`, `.no_below`, `.min_confidence`, `.criteria`, `.otherwise` and `.detail`.
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
  `ERROR`: a failure is raised and marked on the span. The logs API is private in
  `opentelemetry-api`, so it is resolved defensively: a release without it costs the logs
  signal and nothing else, and `events("log")` or `events("both")` is then a `ConfigError`
  where the guide is configured rather than records that silently go nowhere. A sink that
  raises is swallowed, so a logging failure never reaches the caller or the ask span.
- `GuideBuilder.from_env()` applies `TYPESAFE_API_KEY`, `TYPESAFE_BASE_URL` and
  `GUIDEME_MODEL` onto a builder, so a setting with no environment variable can be chained
  after them; `Guide.from_env()` and `AsyncGuide.from_env()` are that step plus `build()`.
- `spec/`: the JSON Schemas and the 42 golden policy vectors vendored from `guideme-rust`, run
  as the conformance suite, with a CI job that fails when this copy drifts from that
  repository's `main`.
