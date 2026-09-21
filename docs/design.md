# Design

`guideme` is a handful of modules behind one verb. Vocabulary used below: a **module** has an
**interface** and an **implementation**; a **seam** is where the interface lives; a module is
**deep** when a lot of behaviour sits behind a small interface.

## Seams

| Module | Interface | What it hides |
|---|---|---|
| `Guide`, `AsyncGuide` (`guide.py`) | `ask(shape, state)`, `models()`, `with_policy`, `GuideBuilder` | request assembly, question-id minting, one span per request, per-answer events, policy precedence, decode of every shape |
| `policy` (`policy.py`) | `resolve(answer, thresholds) -> Outcome`, `Policy`, `Thresholds` | threshold arithmetic for all three primitives, validation, ranking. Pure: no I/O, no caller types. This is the function the shared contract pins. |
| `api.Client`, `api.AsyncClient` (`api/client.py`) | `evaluate`, `models` | HTTP, the auth header, retry with backoff and `retry-after`, status to error mapping, body decode |
| shapes (`ask.py`) | `encode`, `decode` over a question, a tuple, a list or a dict | id assignment in encounter order, per-question settled thresholds, reading the outcomes back into the same shape |
| question kinds (`question.py`) | `noul`, `choose`, `score`, `choose_among`, `score_levels`; `.with_policy`, `.otherwise`, `.detail`, `.criteria` and the three threshold one-liners | wire encoding per primitive, rubric membership checks, outcome to typed value, the unsure ladder |
| enum bases (`enums.py`) | `Choice`, `Levels`, `fallback` | member value to rubric, member name to wire key, the fallback marker, the rule checks that fire where the enum is written |
| typed surface (`_ask_overloads.py`) | one `@overload` of `ask` per supported shape | the mapping from a shape of questions to a shape of answers, generated once for the synchronous and asynchronous classes so they cannot drift |

## The deletion test

- Delete `policy` and threshold logic reappears in every caller, three times, one per
  primitive, and the shared contract disappears.
- Delete `ask` and batching reappears as one method per shape (`batch`, `batch_all`,
  `batch_map`, and so on) with id bookkeeping in each.
- Delete `api.client` and retry with backoff reappears wherever the API is called.
- Delete `Guide` and span assembly, id minting and the precedence merge reappear at every call
  site.
- Delete `enums` and every enum carries a hand-written rubric table that can drift from its
  members.
- Delete `_ask_overloads` and `ask` returns `object`; every call site casts.

Each module survives the test.

## Decisions

- **Questions are values.** `noul(…)`, `choose(C, …)`, `score(L, …)` need no `Guide`: build one
  once, store it as a module constant, ask it as often as you like. Every question type is a
  frozen dataclass and every method returns a new one, so a shared question cannot be mutated
  by the caller that asks it. The `Guide` is only an executor.
- **One verb.** `guide.ask(shape, state)`. Question first, state second, so
  `if guide.ask(noul("…"), ticket)` reads with the judgment early.
- **Batching is the shape.** A tuple of questions is a question. So is a list or a dict. The
  output has the same shape. One request, one span, ids `q0..qN` in encounter order.
- **The typed shapes are enumerated, not computed.** Python has no type-level map over a
  tuple, so the mapping from a shape of questions to a shape of answers is written out as
  overloads: a single question, a list, a dict, tuples of one to eight questions, and tuples of
  one to seven questions followed by one list or dict. `scripts/gen_ask_overloads.py` writes
  them and `mise run gen-check` fails when the committed file is stale. A shape outside that
  list, a tuple nested inside a tuple for instance, works at runtime but is a checker error at
  the call: `No overloads for "ask" match the provided arguments`. There is no annotation that
  silences it, because the error is on the argument; flatten the shape, or ask the inner one
  as its own call.
- **Encounter order for a dict is insertion order.** Rust sorts, because its map is a
  `BTreeMap`. Python's dicts are ordered and insertion order is the idiom, so a caller who
  wants `q0` to be a particular question puts it first.
- **Policy is a patch; thresholds are settled.** `Policy` has optional fields and is frozen, so
  a house policy is a module constant. Precedence is question, then guide, then the defaults
  `0.5 / 0.5 / 0.0`. `Thresholds` validates on construction and is what `resolve` and the
  golden vectors take.
- **The unsure ladder.** `.otherwise(value)` beats the rubric's `fallback(…)` member, which
  beats `UnsureError`. The method is `otherwise` rather than Rust's `or` because `or` is a
  keyword. `.detail()` switches to `Verdict`, `Ranked[C]` or `Scored[L]` and never fails on
  unsure.
- **Score plain output is the argmax level**, ties to the lowest. `.detail()` exposes the API's
  expected `value` too.
- **The enum comes first in the constructor.** `choose(Department, "Which team?")` rather than
  a type argument, because Python has no way to give one at a call site. The instructions come
  second, which also puts the two runtime constructors in the same order as their rubric:
  `choose_among("…", options)`.
- **`Choice` and `Levels` validate where they are written.** `__init_subclass__` runs after the
  members exist, so an empty rubric, a rubric of the wrong size, a non-text value, a second
  `fallback(…)`, a repeated rubric text or a `fallback(…)` on a `Levels` raises `ConfigError` on
  the class statement rather than on the first request. A type checker cannot see inside an enum
  body, so this is the only place those rules can fire.
- **`fallback(…)` is a `Choice` thing.** A score has no member to fall back to, it has an order,
  so the level to use when a score is unsure is `.otherwise(level)` on the question. Marking a
  `Levels` member is a `ConfigError` naming that method, matching the Rust derive, which rejects
  the same mistake at compile time.
- **`fallback(…)` returns a `str` subclass.** The member's value stays its rubric, exactly like
  every other member's, and the marking lives in the type rather than in a second attribute a
  caller could read or set.
- **`Levels` writes its own comparisons.** `IntEnum` would make a level comparable with any
  integer and with any other `IntEnum`, which is the comparison this exists to reject. Four
  dunders typed `(self, other: Self)` make `Frustration.calm >= Urgency.low` a checker error
  instead.
- **The timeout is per phase, not per attempt.** `httpx` gives connecting, writing, reading and
  pool acquisition the whole of `timeout(…)` each, so a slow attempt can outlast it several
  times over; Rust's `reqwest` applies one deadline to the attempt. The two SDKs differ here,
  and guideme documents the difference rather than building a deadline `httpx` does not have.
- **Async is asyncio.** `AsyncGuide` sleeps with `asyncio.sleep` and holds an
  `httpx.AsyncClient`. No `anyio` dependency, and the test suite runs coroutines with
  `asyncio.run` rather than adding a pytest plugin.
- **Jitter comes from the standard library.** `random.SystemRandom`, so backoff needs no extra
  dependency and does not disturb a caller who seeded the global `random`.
- **Telemetry speaks OpenTelemetry.** The ask span uses the GenAI conventions, each HTTP
  attempt is its own client span with the HTTP conventions, and a failure is `error.type` plus
  an error span status rather than an error-level record. Anything without a convention is
  namespaced `guideme.`. The package depends on `opentelemetry-api` only and installs no
  provider; `docs/observability.md` shows the exporter side.
- **An answer is a span event and a log record, and the caller picks.** Rust emits one
  `tracing` event and lets the subscriber fan it out, so its example filters events off the
  span exporter to store each one once. There is no subscriber here, so the library makes both
  calls and `events(...)` is the filter the application would otherwise have written. `both` is
  the default because a record costs nothing until a `LoggerProvider` exists, which keeps the
  library's unconfigured behaviour the same as Rust's. The mode rides on `_Config`, so a
  `with_policy` copy keeps it and `Guide` and `AsyncGuide` cannot differ.

## Sharp edges

- **A batch is atomic.** One answer that resolves to `UnsureError`, or to `ProtocolError`,
  fails the whole call. Use `.otherwise(…)`, an enum fallback, or `.detail()` on the questions
  that may be unsure.
- **Ids are visible.** `q{n}` appears on the wire, in `UnsureError` and in every
  `guideme.answer`, on both signals. They are positions in encounter order, nothing more.
- **Correlation is the active span, not an argument.** A log record resolves the OpenTelemetry
  context when it is built, so a record built outside the `with` block that opened the span
  would silently lose its ids. Every emit stays inside it.
- **Two members with the same text are one member.** Python's `Enum` makes the second an alias
  of the first, which would leave a three-option rubric with two options and a marked fallback
  that `fallback_member()` cannot find. A repeated rubric text is a `ConfigError` on the class
  statement, naming the members that repeat.
- **`Key` and `Rank`** are only meaningful through `choose_among` and `score_levels`. They are
  `NewType`s over `str` and `int`, so nothing else hands you one.
- **The four scalars are brands, not validated types.** `Probability`, `Confidence`, `Key` and
  `Rank` are `NewType`s, so `Probability(2.0)` and `Rank(99)` are accepted by the checker and by
  the interpreter alike. What makes them trustworthy is that only the wire mints them, and it
  validates there: a probability outside `0..=1` is a `ProtocolError` at parse time, an option or
  a level outside the rubric is one at decode time. A caller who constructs one by hand is
  outside that guarantee and gets no error saying so.
- **`__init__` is not the surface.** `Guide(client, config)` and `AsyncGuide(client, config)`
  name the internal `Client` and the private `_Config`, because Python has no private
  constructor, not because either is supported. Build one through `Guide.builder()` or
  `Guide.from_env()`; those are what validate the policy and the origin before a socket opens.
- **`with_policy` shares the pool.** The copy holds the same client, so `close()` on either the
  original or the copy closes the connection pool for both.
- **`Question` is supported, and not in `__all__`.** The top-level surface is a fixed list and
  a question's type is whatever its constructor returns, so callers annotate by inference.
  `guideme.question` is the second tier, beside `guideme.api` and `guideme.policy`, and
  carries the same promise: import `Question` from there when you need to write the type of a
  stored question down.
- **Two `Question`s.** `guideme.question.Question` is the user-facing value;
  `guideme.api`'s question model is the wire shape it becomes.
- **State is JSON-shaped.** Anything `json.dumps` accepts without a default hook. A dataclass
  goes through `dataclasses.asdict`, a pydantic model through `.model_dump()`. This is the one
  untyped value in the package, and it is serialised at the boundary.
- **Fallback use is not on the answer event.** The event is emitted from the resolved outcome,
  before typed decoding chooses `.otherwise(…)` or the enum's fallback member. The settled
  thresholds are on the event, so "why unsure" is still answerable.
