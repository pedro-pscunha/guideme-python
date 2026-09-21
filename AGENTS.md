# guideme working rules

Rules for anyone (or anything) changing this repository. Read fully before editing.

## What this is

A Python package that makes a TypeSafe Jev judgment usable as control flow: a yes/no is an
`if`, a choice is an exhaustive `match`, a score is a comparison. One distribution, `guideme`,
published to PyPI under `MIT OR Apache-2.0`. The public surface is exactly the names listed in
`__all__` in `src/guideme/__init__.py`, nothing else.

That surface is a published API. Anything removed or renamed in it is a breaking change for
people who do not work here, so it needs a major bump and a `CHANGELOG.md` entry.

This is not a translation of the Rust SDK. It is written in Python's idiom and satisfies the
same published contract. The contract lives in
[`guideme-rust`](https://github.com/pedro-pscunha/guideme-rust): `docs/contract.md` states it
and `spec/` pins it. The live TypeSafe docs are the source of truth for the wire itself:
`https://docs.typesafe.ai/api.md`.

## Layout and seams

| Path | Owns | Rule |
|---|---|---|
| `src/guideme/_json.py` | `Json` and one compact `dumps` | the only untyped edge; the caller's data, serialised at the boundary |
| `src/guideme/scalars.py` | `Probability`, `Confidence`, `Key`, `Rank`, `ApiKey`, `Model` | validation happens once, here; `ApiKey` never prints |
| `src/guideme/errors.py` | the `GuidemeError` tree and `kind` | `kind` is the cross-SDK name and the `error.type` value; imports nothing from `guideme` |
| `src/guideme/policy.py` | `resolve`, `Policy`, `Thresholds`, `Verdict`, the answer and outcome dataclasses | pure: no I/O, no caller enums, keys and level indices only |
| `src/guideme/enums.py` | `Choice`, `Levels`, `fallback` | a member's name is its wire key and its value is its rubric; both validate at class definition |
| `src/guideme/question.py` | question kinds, constructors, `Ranked`, `Scored`, the unsure ladder | a question is inert until asked; the reader travels with it |
| `src/guideme/ask.py` | shapes: `encode`, `decode`, `Plan` | ids are `q0..qN` in encounter order, insertion order for a dict |
| `src/guideme/_ask_overloads.py` | the typed `ask` surfaces | GENERATED; edit `scripts/gen_ask_overloads.py` and run `mise run gen` |
| `spec/` | the vendored schemas and golden vectors | read-only here; it is `guideme-rust`'s output, and `mise run spec-check` proves this copy matches |

Modules keep a one-way import graph, which `pyright`'s `reportImportCycles` enforces:
`errors` imports nothing from the package; `_json` and `scalars` import `errors`; `policy`
imports `errors` and `scalars`; `enums` imports `errors` and `policy`; `question` imports the
above; `ask` imports `question`; `_ask_overloads` imports `_json` and `question`.

## Invariants

These hold everywhere in `src/guideme`. `ruff`, `pyright` and `pylint` enforce most of them;
the rest are checked in review.

- **Every suppression names its rule and its reason, on the same line.** `# noqa: RULE -- why`,
  `# pyright: ignore[rule] -- why`, `# pylint: disable=rule; why`. A bare `# type: ignore` is
  forbidden, and `reportUnnecessaryTypeIgnoreComment` fails a suppression that stopped applying.
- **No `Any`.** The only untyped value is the caller's state and instructions, typed as `Json`
  and serialised at the boundary. `pyright` 1.1.414 has no `reportAny`, so this one is held by
  `ruff`'s `ANN401`, by `pyright` strict's `reportUnknown*` family, and in review.
- **No `assert` in `src`, no `except Exception: pass`, no `cast()` without a comment proving
  the invariant it stands on.** Raise a typed `GuidemeError`; its `kind` is one of `auth`,
  `invalid`, `rate_limited`, `overloaded`, `transport`, `unexpected_status`, `protocol`,
  `unsure`, `config`, the same strings every other guideme SDK returns.
- **No `case _:` on this package's own enums or unions.** Add a member and update every match;
  `reportMatchNotExhaustive` is an error. The one catch-all in `ask.encode` is over `object`
  and carries a comment saying so: it is how a value that is not a shape gets rejected.
- **No lossy numeric conversion.** Probabilities and confidences are parsed once, at the wire,
  into `Probability` and `Confidence`.
- **Fail loudly.** An unknown answer kind, an option or level outside the rubric, a malformed
  body, bad thresholds, an empty batch, a duplicate key: each is a typed error. Never a
  default, never a log-and-continue.
- **The API key is never printed.** `ApiKey` prints as `ApiKey(***)` through both `repr` and
  `str`, is not JSON serialisable, refuses to pickle, is on no span, and appears in no error.
- **State is user data.** Its content never reaches a span unless `record_state(True)` was set;
  its length, `guideme.state.bytes`, always does.
- **Telemetry names are the contract.** They come from the OpenTelemetry semantic conventions
  where one exists (`gen_ai.*`, `http.*`, `server.*`, `url.*`, `error.type`) and are namespaced
  `guideme.` otherwise. Numbers are `int`. A failure sets `error.type` and an ERROR span status;
  no ERROR-level record is ever emitted and no exception event is ever recorded.
- **The library installs nothing.** No tracer provider, no exporter, no logging handler.
- **Every public item has a docstring** (`ruff`'s `D` rules, Google style). A `Choice` or
  `Levels` member's docstring is not its rubric; its value is.
- **Dependencies stay minimal.** Runtime is `httpx`, `pydantic`, `opentelemetry-api`. Adding one
  needs a reason in the commit message. Jitter uses `random.SystemRandom`, not a new dependency.

## Policy semantics (do not change casually)

- Noul: `p >= yes_above` is yes, `p <= no_below` is no, strictly between is unsure.
  Defaults `0.5 / 0.5`.
- Choice and score: `confidence < min_confidence` is unsure. Default `0.0`.
- Precedence: the question's own patch, then the guide's, then the defaults.
- Unsure ladder: `.otherwise(value)`, then the rubric's `fallback(...)` member, then
  `UnsureError`. `.detail()` never fails and drops any `.otherwise(...)`.
- Score plain output is the argmax level, ties to the lowest; `.detail()` also exposes the
  API's expected `value`.
- A batch is atomic.

A change to any of these changes `spec/vectors/policy.json` and therefore every other SDK. It
is made in `guideme-rust` first, not here.

## Tests

Few tests, high grade. The ceiling is 40 test functions; a parametrised function counts once.
A new test must be one of:

- a property test (`hypothesis`) over a law of `policy.resolve`, the shapes, or the wire types;
- a wire or contract check through a real local HTTP server (`pytest-httpserver`), asserting on
  the received request and on typed results;
- a structural tracing assertion through an in-memory span exporter, on field names and values;
- a typing proof (`typing.assert_type` under `tests/typing/`) or a `pyright` negative under
  `tests/typing/expect_errors/`;
- a redaction proof.

Never assert on log or `repr` text, except to prove a secret is absent. Never mock `policy`,
`Guide` or the transport.

The live tests hit the real API. They are marked `live`, deselected by default, and run only on
purpose:

```
TYPESAFE_API_KEY=… uv run --locked pytest -m live
```

CI holds no key and never runs them.

## Commands

```
mise run sync        # install the locked environment
mise run check       # the gate: fmt-check, gen-check, ruff, pyright, pylint, pytest, build, audit
mise run test        # pytest only
mise run lint        # ruff only
mise run types       # pyright only
mise run gen         # regenerate src/guideme/_ask_overloads.py
mise run spec-check  # fail if spec/ differs from guideme-rust main
mise run hooks       # activate the tracked git hooks in .githooks
```

Run everything from the repository root. Capture long output to a file; do not pipe a gate
through `tail`.

## Git

- Branch from `main`, open a pull request, squash-merge. `main` takes pull requests only: a
  GitHub ruleset requires every CI check to pass and refuses direct pushes.
- CI (`.github/workflows/ci.yml`) runs the same gate on Python 3.12 and 3.14, scans the whole
  history with `gitleaks`, lints and type-checks `examples/otlp`, proves the 3.12.0 floor, and
  checks `spec/` against `guideme-rust`. `advisories.yml` re-audits the unchanged lock file
  weekly and `spec-drift.yml` re-checks the contract weekly. `.github/dependabot.yml` is what
  moves the SHA-pinned actions and the pinned tools forward. CI holds no secrets.
- The hooks are tracked in `.githooks/` and do nothing until you run `mise run hooks`, which
  points this repository's `core.hooksPath` at that directory and fails, leaving nothing
  changed, if the result is not active. Git reads one hooks directory and a repo-local
  `core.hooksPath` outranks a global one, so these fire even where you have a global hooks
  directory, which also means a global secret-scanning hook stops running here, and is why
  these hooks scan for secrets themselves.
- `pre-commit`: `gitleaks` on the staged change, then `mise run fmt-check`, `mise run lint` and
  `mise run gen-check`. `pre-push`: `gitleaks` over every range git reports as being pushed, so
  a branch other than the checked-out one is scanned too, then `mise run check` when a branch
  with content is pushed. A delete or a tag-only push runs no gate.
- Every tool a hook needs is resolved loudly. A missing `gitleaks` or `mise` refuses the commit
  or the push; no hook ever skips a check because a binary was not on `PATH`. A scanner that
  fails to run is reported as that, not as a finding.
- `--no-verify` skips a hook. Two known limits are not escape hatches but read like them: the
  gate inspects the working tree, not the index or the pushed commit, so a partial `git add -p`
  is checked against the files on disk; and a checkout of a commit older than `.githooks/` has
  no hooks at all. CI on every pull request is the backstop for both.
- Commit messages: imperative subject under 72 characters, body says why. No trailers, no tool
  attributions, no generated-by lines.
- Never commit an API key, a `.env`, or anything under `.tmp/`.

## Changing the contract

The contract is published by `guideme-rust`. A change to it is made there first, and reaches
this repository as a new `spec/`.

1. Read the current TypeSafe API page.
2. Land the change in `guideme-rust`: the wire mirror, `resolve`, the vectors, `docs/contract.md`.
3. Here: re-vendor `spec/`, write the new commit into `spec/SOURCE`, and run `mise run spec-check`.
4. Mirror the behaviour change in `src/guideme`, with the tests that prove it.
5. `mise run check`, then note it in `CHANGELOG.md`.

Renaming, adding or removing a span or event field is also a contract change, and it is
announced to every other SDK the same way. `spec/` is unaffected by it.

## Other SDKs

Each guideme SDK lives in its own repository and is written from scratch in its own language.
They agree because they satisfy the same `spec/`, not because they share code. A behaviour
difference found here is either a bug here or a contract ambiguity to be resolved in
`guideme-rust`; it is never fixed by quietly diverging.
