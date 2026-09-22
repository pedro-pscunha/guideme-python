# guideme working rules

Rules for anyone (or anything) changing this repository. Read fully before editing.

## What this is

A Python package that makes a TypeSafe Jev judgment usable as control flow: a yes/no is an
`if`, a choice is an exhaustive `match`, a score is a comparison. One distribution, `guideme`,
published to PyPI under `MIT OR Apache-2.0`. The public surface has two tiers:

- the 35 names in `__all__` in `src/guideme/__init__.py`, imported from `guideme` itself;
- `guideme.api` and `guideme.policy` as whole modules, imported by their own path and not
  re-exported at the top level: `guideme.api` is the wire mirror and `guideme.api.client`
  holds `Client` and `AsyncClient`, and `guideme.policy` holds `resolve`.
- one name from a third module, `guideme.question.Question`: what every constructor in the
  first tier returns, and the only way to write the type of a stored question down. The
  promise covers that name and nothing else in `guideme.question` — `validate`, `Spec` and
  the concrete question classes stay private, because a tier is a promise and this is the
  narrowest one that lets a caller annotate. The README's **Lower layers** section documents
  all of it.

Everything else in the package is private, whatever its name looks like.

Both tiers are a published API. Anything removed or renamed in either is a breaking change for
people who do not work here, so it needs a major bump and a `CHANGELOG.md` entry.

This is not a translation of the Rust SDK. It is written in Python's idiom and satisfies the
same published contract. The contract lives in
[`guideme-rust`](https://github.com/pedro-pscunha/guideme-rust): `docs/contract.md` states it
and `spec/` pins it. The live TypeSafe docs are the source of truth for the wire itself, over
two pages: `https://docs.typesafe.ai/api.md` covers `POST /v1/systemone` and
`https://docs.typesafe.ai/models.md` covers `GET /v1/models`. Read both before touching
`src/guideme/api/`.

## Layout and seams

| Path | Owns | Rule |
|---|---|---|
| `src/guideme/_json.py` | `Json` and one compact `dumps` | the only untyped edge; the caller's data, serialised at the boundary |
| `src/guideme/scalars.py` | `Probability`, `Confidence`, `Key`, `Rank`, `ApiKey`, `Model` | validation happens once, here; `ApiKey` never prints |
| `src/guideme/errors.py` | the `GuidemeError` tree and `kind` | `kind` is the cross-SDK name and the `error.type` value; imports nothing from `guideme` |
| `src/guideme/policy.py` | `resolve`, `Policy`, `Thresholds`, `Verdict`, the answer and outcome dataclasses | pure: no I/O, no caller enums, keys and level indices only |
| `src/guideme/enums.py` | `Choice`, `Levels`, `option`, `level`, `fallback`, and the internals `render` and the `require_*` checks | a member's name is its wire key and its value is its rubric; both validate at class definition, and a repeated rubric text is refused there. `render` is the one place a rubric's examples become wire text, and its output is a cross-SDK contract item. `render`, `require_unshared_examples`, `require_no_counterexamples` and `require_no_fallback` are internal despite their names: they are imported by `question.py` and are in no tier, like `question.validate` |
| `src/guideme/question.py` | question kinds, constructors, `Ranked`, `Scored`, the unsure ladder | a question is inert until asked; the reader travels with it |
| `src/guideme/ask.py` | shapes: `encode`, `decode`, `Plan` | ids are `q0..qN` in encounter order, insertion order for a dict |
| `src/guideme/_ask_overloads.py` | the typed `ask` surfaces | GENERATED; edit `scripts/gen_ask_overloads.py` and run `mise run gen` |
| `src/guideme/telemetry.py` | every span, event, log record and attribute | the names are the contract, documented in `docs/observability.md`; installs no provider |
| `src/guideme/api/__init__.py` | the wire mirror and the adapters to the core | mirrors `spec/schema/*.json` field for field; no policy here |
| `src/guideme/api/client.py` | HTTP, retries, statuses to errors, one span per attempt | the only importer of `httpx`; every decision it makes is made by the pure `step` |
| `src/guideme/guide.py` | `Guide`, `AsyncGuide`, `GuideBuilder`, `ModelInfo` | the two executors share `_prepare` and `_finish`; what is written twice is the two `await`s |
| `spec/` | the vendored schemas and golden vectors | read-only here; it is `guideme-rust`'s output, and `mise run spec-check` proves this copy matches |

Modules keep a one-way import graph, which `pyright`'s `reportImportCycles` enforces:
`errors` imports nothing from the package; `_json` and `scalars` import `errors`; `policy`
imports `errors` and `scalars`; `enums` imports `errors` and `policy`; `question` imports the
above; `ask` imports `question`; `_ask_overloads` imports `_json` and `question`; `telemetry`
imports `errors` and `policy`; `api` imports `question` and below; `api.client` imports `api`
and `telemetry`. Nothing inside the package writes `from guideme import ...`: that would
import the package's own `__init__`, which imports the executors, which import `api`.

`docs/design.md` records the decisions and the sharp edges. Update it when a decision changes.
`docs/contract.md` says where the cross-SDK contract lives and how drift from it is caught.

`examples/` holds runnable programs. Each is its own uv project with its own lock file, so the
root gate lints and type-checks them but never resolves them, and their dependencies stay out
of the library's. Run one with `uv run main.py` inside its directory. CI does resolve
`examples/otlp` with `--locked`, and its lock file pins `guideme` through a path dependency, so
a change to the library's dependency set **or its version** must refresh
`examples/otlp/uv.lock` in the same pull request: `uv lock` inside `examples/otlp` rewrites it.

## Invariants

These hold everywhere in `src/guideme`. `ruff`, `pyright` and `pylint` enforce most of them;
the rest are checked in review.

- **Every suppression names its rule and its reason, on the same line.** `# noqa: RULE -- why`,
  `# pyright: ignore[rule] -- why`, `# pylint: disable=rule  # why`. pylint reads everything
  after `disable=` as rule names, so its reason goes in a second comment, not after a
  semicolon. A bare `# type: ignore` is
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
  default, never a log-and-continue. There is exactly one deliberate exception, written as an
  explicit `except Exception` in `telemetry.Logs._emit` so that a sweep for one finds it: an
  OTLP log record whose sink raises is swallowed. The provider, the processor and the exporter
  are the application's, an ask that reached its answer must return it, and a span saying the
  ask succeeded must not be contradicted by a failure to store a log. That failure is visible
  where it belongs, in the application's own logging pipeline. The guard covers that call and
  nothing else: the record is built before it, so a `LogRecord` this package cannot construct
  is caught in `resolve_logs` and makes the signal absent, loudly, instead of being swallowed
  once per answer. Nothing else in `src` swallows anything.
- **A rubric's text is unique, and a rubric is the caller's to get wrong once.** Two members of
  a `Choice` or a `Levels` sharing a value are aliases in Python, not two options, so a repeat
  is a `ConfigError` on the class statement; so is `fallback(…)` on a `Levels`, which has
  `.otherwise(level)` instead. Both fire where the enum is written, like the Rust derive's
  compile errors.
- **A rubric's examples are composed into its text at the wire, and nowhere else.** A rubric's
  value stays the bare text; `enums.render` is what turns an `option(…)`, `level(…)` or
  `fallback(…)` into what the model reads, and every site that puts a rubric on the wire —
  `Choice.rubric()`, `Levels.levels()`, `choose_among`, `score_levels` and
  `NoulQuestion.criteria` — goes through it, so a rubric cannot arrive having quietly lost what
  it carries. All three kinds of question take examples: a yes and a no are as confusable as
  two options, and `option(…)` is the one constructor for a described alternative, so there is
  no fourth. A bare string and a rubric with no examples render to their own bytes, so an
  existing caller's request does not move. The rendered string is a contract item shared with
  every other SDK, stated in `docs/contract.md` and pinned by `spec/vectors/rubric.json`, and
  so is the order: examples and counterexamples render in the order written, never sorted and
  never de-duplicated into a set.
- **A rubric's examples must be consistent, and that is checked where it is written.** A blank
  or whitespace-only entry, a repeat within one clause, a clause given as one string rather
  than a sequence of them, an empty clause written out (`examples` and `counterexamples`
  default to `None`, so any empty sequence was typed), examples attached to a blank rubric,
  a `fallback(…)` on a runtime path, and a counterexample on a level are each a `ConfigError`.
  A blank rubric that carries no examples is **not** an error: it means what it meant in 0.1.0,
  and this release does not redefine it. So are the two contradictions: one
  string as an example of two alternatives of the same question, and one string as both an
  example and a counterexample of the same alternative. One string as an example of one
  alternative and a counterexample of another is **legal and required** — it is the confusable
  pattern the feature exists for, and `tests/test_rubrics.py` sends it on the wire.
- **Nothing outside `api/` may see a `pydantic` exception.** A caller's state or instructions
  that pydantic refuses leaves `api/` as a `ConfigError`, and a `NaN` or an infinity is refused
  rather than serialised as `null`.
- **The API key is never printed.** `ApiKey` prints as `ApiKey(***)` through both `repr` and
  `str`, is not JSON serialisable, refuses to pickle, is on no span, and appears in no error.
- **State is user data.** Its content never reaches a span unless `record_state(True)` was set;
  its length, `guideme.state.bytes`, always does.
- **Telemetry names are the contract.** They come from the OpenTelemetry semantic conventions
  where one exists (`gen_ai.*`, `http.*`, `server.*`, `url.*`, `error.type`) and are namespaced
  `guideme.` otherwise. Numbers are `int`. An answer and a retry reach two signals, a span event
  and an OTLP log record, and carry identical attributes on both; the record adds the severity
  and the message the Rust SDK writes, and resolves its trace and span ids from the span it is
  emitted inside. `events(...)` turns either signal off, and a mode that is not `span`, `log`
  or `both` is a `ConfigError`. A failure sets `error.type` and an ERROR span status; no
  ERROR-level log record is ever emitted and no exception event is ever recorded.
- **The traces API is required; the logs API is not.** `opentelemetry-api` keeps the logs API
  at the private `opentelemetry._logs`, and this package accepts a wide range of that
  distribution, so `telemetry.resolve_logs` imports it defensively and its absence is a value.
  A release that moves it must cost the logs signal and nothing else: `import guideme` still
  works, the traces signal is untouched, and `events("span")` still routes every answer and
  retry. Every read of that API lives inside the one guard — the import, both severity
  members, both `get_logger` calls and a throwaway `LogRecord` built with the five keywords
  `Logs._emit` uses — and `ImportError`, `AttributeError` and `TypeError` all mean the same
  thing there. A guard narrower than the promise is the promise being false: a module that
  still imports and has lost a member would otherwise kill `import guideme` at module scope. Asking for the signal that is not there, `events("log")` or `events("both")`, is a
  `ConfigError` naming the version and both ways out. The default `"both"` is not such an ask,
  so it degrades to the span alone, which is all an application without the logs API could have
  exported anyway. `latest-deps` in CI resolves the range unpinned so a move is seen early; it
  is deliberately not a required check, because a third party's release must not block a merge.
- **The library installs nothing.** No tracer provider, no logger provider, no exporter, no
  logging handler.
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

Few tests, high grade. The ceiling is 47 test functions; a parametrised function counts once.
It was 40 before the logs signal, which is user-requested scope that the span assertions could
not cover: correlation, severity and routing each need a record to look at. The forty-third is
the pre-publish proof that a log sink which raises reaches neither the caller nor the ask span:
it asserts the absence of a failure on a path where every other test asserts a presence, so no
existing test could carry it. Rubric examples added the last four, also user-requested scope:
the golden rendering table, the passthrough property that proves 0.1.0's bytes have not moved,
the wire proof that a rendered rubric reaches the request, and a live proof that examples move
the distribution — one function covering a choice and a noul, because both are the same
invariant and a second function would buy nothing. Each asserts a different thing about a
string no existing test looks at.
Everything else those two passes added went into a parameter of a test that was already there.
A new test must be one of:

- a property test (`hypothesis`) over a law of `policy.resolve`, the shapes, or the wire types;
- a wire or contract check through a real local HTTP server (`pytest-httpserver`), asserting on
  the received request and on typed results;
- a structural tracing assertion through an in-memory span exporter, or an in-memory log
  record exporter, on field names and values;
- a typing proof (`typing.assert_type` under `tests/typing/`) or a `pyright` negative under
  `tests/typing/expect_errors/`;
- a structural import-boundary proof (AST) over `src/guideme`;
- a redaction proof.

Two of the illegal states this package refuses cannot be checker errors: an enum body is opaque
to pyright, so a `Choice` with two `fallback` members and a `Levels` with one member are a
`ConfigError` raised at class definition, proven in `tests/test_enums.py`. Every other negative
stays a `pyright` failure under `tests/typing/expect_errors/`.

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
mise run build       # sdist + wheel, twine check, and the sdist carries docs, examples, tests
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
- CI (`.github/workflows/ci.yml`) runs the same gate on Python 3.12 and 3.14, installs the
  wheel it built into an empty environment and imports it, scans the whole history with
  `gitleaks`, lints and type-checks `examples/otlp`, proves the 3.12.0 floor, resolves the
  dependency ranges unpinned in `latest-deps`, and checks `spec/` against `guideme-rust`.
  `latest-deps` is the one job that is not a required check. `advisories.yml` re-audits the
  unchanged lock file
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

## Releasing

`guideme` is published to PyPI by `.github/workflows/release.yml`, which a `v*` tag starts.
Its `publish` job uses PyPI trusted publishing: PyPI trades the job's OIDC token for a
short-lived upload token, so no PyPI credential exists in this repository's secrets and none
should ever be added. PyPI checks four values, which are registered once by hand on the
project's publishing page and must match the workflow exactly:

| Field | Value |
|---|---|
| Owner | `pedro-pscunha` |
| Repository | `guideme-python` |
| Workflow | `release.yml` |
| Environment | `pypi` |

**A published version is permanent.** It can be yanked, which stops new installs resolving to
it and leaves existing lock files alone, but it can never be deleted or replaced. Get the gate
green before the tag, not after.

1. Bump `version` in `pyproject.toml`. Then `uv lock` at the root and `uv lock` inside
   `examples/otlp`: both lock files record the version, and both are resolved with `--locked`.
2. Move the `Unreleased` notes in `CHANGELOG.md` under the new version with today's date.
3. Leave the **What arrives** capture in `docs/observability.md` alone unless you re-take it.
   Its `InstrumentationScope guideme X.Y.Z` lines carry the version the run actually emitted,
   and the prose above it names that version, so a reader can tell the capture's age from a
   claim about today. Do not edit the version forward to match a release no run produced, and
   do not delete it either: that loses the provenance permanently. Re-take the capture and
   update both together, or change nothing.
4. `mise run check`, then open a pull request and squash-merge it with CI green.
5. On `main`, at that commit: `git tag -a vX.Y.Z -m vX.Y.Z` and `git push origin vX.Y.Z`. The
   tag ruleset refuses a tag that is later moved or deleted, so tag the commit you mean.
6. The tag starts the release workflow: `build` runs the whole gate and `uv build`, `publish`
   uploads `dist/` from the `pypi` environment. Watch it with `gh run watch`.
7. Check it landed: `https://pypi.org/project/guideme/X.Y.Z/`, and that
   `uv pip install guideme==X.Y.Z` resolves in a fresh environment.

## Other SDKs

Each guideme SDK lives in its own repository and is written from scratch in its own language.
They agree because they satisfy the same `spec/`, not because they share code. A behaviour
difference found here is either a bug here or a contract ambiguity to be resolved in
`guideme-rust`; it is never fixed by quietly diverging.
