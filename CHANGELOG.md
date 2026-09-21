# Changelog

## Unreleased

First release, in progress. Nothing is on PyPI yet.

- Questions as values: `noul`, `choose`, `score`, `choose_among`, `score_levels`, with
  `.yes_above`, `.no_below`, `.min_confidence`, `.criteria`, `.otherwise` and `.detail`.
- `Choice` and `Levels` enum bases: a member's name is its wire key and its value is its
  rubric, `fallback(...)` marks the member to use when the policy says unsure, and both
  validate where the enum is written.
- Pure `policy.resolve` with `Policy` patches and validated `Thresholds`; the unsure ladder is
  `.otherwise(value)`, then the rubric's fallback member, then `UnsureError`.
- Shapes: a question, or a tuple, list or dict of shapes, answered in one request with ids
  `q0..qN` in encounter order. The typed forms are generated overloads, so the synchronous and
  asynchronous surfaces cannot drift.
- `spec/`: the JSON Schemas and the 42 golden policy vectors vendored from `guideme-rust`, run
  as the conformance suite, with a CI job that fails when this copy drifts from that
  repository's `main`.
