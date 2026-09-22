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
- **Only a failed connection is resent, and no timeout ever is.** `httpx.ConnectError` means
  the request did not arrive, so nothing was judged and a resend repeats nothing. A
  `RemoteProtocolError` and a body that will not decode mean it did arrive: the API may have
  answered and billed it, and asking again would buy the same judgment twice. Idempotency is
  the line, not whether the failure looks transient.
  `httpx.ConnectTimeout` is the interesting exclusion, because idempotency alone would let it
  through. It is excluded because the contract is shared and Rust cannot draw that line:
  `reqwest` sets one deadline over the attempt, so a connect-phase timeout is `is_timeout()`
  there and not `is_connect()`. An SDK that resent a failure the other could not even see
  would be the two disagreeing about one incident. The second reason stands on its own: a
  retried timeout multiplies the wall time `timeout(…)` is set to bound, and `httpx` already
  spends that budget per phase, so the worst case is long enough without resending it.
- **A transport is injectable and refuses a timeout beside it.** `transport(…)` gives a caller
  a proxy, a client certificate or an `httpx.MockTransport`, which is what makes their own
  control flow testable without a server. `httpx` hands a transport the client's timeout as a
  request extension it may ignore — `MockTransport` does — so a timeout set beside one is a
  promise nothing keeps. It is a `ConfigError` in either order rather than a silent override.
- **`Receipt` has its own module, and declares its own `Usage`.** `_ask_overloads` names
  `Receipt` in a return type and `guide` imports `_ask_overloads`, so it cannot live beside
  `ModelInfo` in `guide` without a cycle. `guideme.receipt` imports nothing from the package,
  which is what lets it sit that low: its `Usage` is a frozen dataclass of two `int`s, copied
  out of `guideme.api.Usage` in `_receipt` the same way `_described` copies `ModelEntry` into
  `ModelInfo`. The wire model keeps its name inside `guideme.api`. Exporting the pydantic one
  would have saved a copy of two integers and put a dependency's whole surface — 28 attributes
  that are not guideme's — on a published type, which is the thing `ModelInfo` exists to stop.
- **Telemetry speaks OpenTelemetry.** The ask span uses the GenAI conventions, each HTTP
  attempt is its own client span with the HTTP conventions, and a failure is `error.type` plus
  an error span status rather than an error-level record. Anything without a convention is
  namespaced `guideme.`. The package depends on `opentelemetry-api` only and installs no
  provider; `docs/observability.md` shows the exporter side.
- **A rubric's examples are flattened into its text, not sent as structured criteria.** The
  TypeSafe API takes structured `criteria`, and `docs.typesafe.ai/primitives/choice.md`
  documents exactly the `what` / `not_for` / `examples` object this surface wants. It is not
  used, for three measured reasons. A score answer echoes its criteria back in `legend`, which
  is `dict[str, str]` here and `BTreeMap<u8, String>` in Rust; object criteria come back as
  objects and fail to parse, so sending them means a breaking change to a public type — in a
  field neither SDK reads beyond its length. Flattening is as good: on the docs' own worked
  example, flattened scored 1.01 against structured's 1.03 at a higher confidence, and on an
  ambiguous choice both reached the option that bare strings miss, inside run-to-run variance.
  And flattening is cheaper: identical content billed 400 input tokens flattened against 450
  structured. The gain comes from the examples being present, not from the JSON shape. So
  `option(…)`, `level(…)` and `fallback(…)` carry the parts on a `str` subclass and `render`
  composes them where the rubric becomes wire text — the wire schema, `spec/`, and every
  existing golden vector untouched.
- **The rendered rubric is a contract item, and the renderer has one entry per wire site.**
  Clauses join with a newline and items with `"; "`, in the order written, and the text is used
  verbatim: a newline rather than a space is what removes the need for a punctuation rule,
  since an example ending in `?` would otherwise render as `Where is my refund?.`. The
  `Not this option` label was measured against `Not` and `Counterexamples` and won.
  `Choice.rubric()`, `Levels.levels()`, `choose_among`, `score_levels` and
  `NoulQuestion.criteria` all render, so a value that reached a runtime constructor cannot
  silently lose its examples. Rust renders the same string, which leaves one deliberate
  asymmetry: `choose_among` here takes an `option(…)`, while Rust's equivalent keeps taking a
  plain string rather than risk inference breakage for existing callers. Equivalent inputs put
  identical bytes on the wire.
- **A noul's criteria take examples too, and through the same `option(…)`.** A yes and a no are
  two described alternatives of one question, exactly as confusable as two options of a choice,
  and measurement says so: asked whether a nightly export job with a manual workaround is
  urgent, plain `Urgent` / `Not urgent` answers yes at 0.75 four times running, and the same
  criteria carrying examples answer no at 0.17. The examples move it to the correct answer,
  because one of the no examples is the situation the state describes. So there is no fourth
  constructor — `option(…)` is "a described alternative" and serves both — and `.criteria(…)`
  renders like every other wire site.
- **Contradictory examples are refused where they are written.** One string offered as an
  example of two alternatives of the same question says an input belongs to both, which cannot
  be true; one string offered as both an example and a counterexample of the same alternative
  says it does and does not belong. Both are a `ConfigError`. The overlap that looks similar
  and is the whole point stays legal: the same string as an example of one alternative and a
  counterexample of another is how two confusable ones are told apart.
- **An answer is a span event and a log record, and the caller picks.** Rust emits one
  `tracing` event and lets the subscriber fan it out, so its example filters events off the
  span exporter to store each one once. There is no subscriber here, so the library makes both
  calls and `events(...)` is the filter the application would otherwise have written. `both` is
  the default because a record costs nothing until a `LoggerProvider` exists, which keeps the
  library's unconfigured behaviour the same as Rust's. The mode rides on the client, which is
  what emits the retries, and the guide reads `client.events` back for the answers, so it has
  one owner. A `with_policy` copy shares the client and therefore the mode, and `Guide` and
  `AsyncGuide` cannot differ.

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
- **A rubric's value is its bare text, examples or not.** `option("x", examples=[…])` still
  equals `"x"`, so two options whose text matches are still one member however their examples
  differ, and a member's `.value` still reads as it was written. The expansion happens only in
  the request.
- **A blank rubric is an error only when examples are attached to it.** `option("   ")` on its
  own is accepted and means exactly what a bare `""` member has always meant;
  `option("   ", examples=[…])` is a `ConfigError`. The first version of this refused any blank
  rubric written through the new constructors, which read as tidy and was wrong: it made a
  declaration that was legal in 0.1.0 illegal in a patch release, on a degenerate input that
  was already meaningless. "You attached examples to nothing" is the real mistake; "your
  description is blank" is not one this release gets to invent. `guideme-rust` narrowed the
  same rule from the same starting point, so a reader of both finds one rule: strict where
  examples are, untouched where they are not. Revisit at a major bump, together.
- **A clause left out is `None`; an empty one was written on purpose.** `examples` and
  `counterexamples` default to `None`, which is how "there are none" is said, so any empty
  sequence that arrives was typed by the caller and says nothing — a `ConfigError`, whatever
  its type, `[]` and `()` alike. The alternative, defaulting to `()` and telling the two apart
  by identity, would have rested on CPython interning the empty tuple: correct today, and
  silently wrong on a runtime that does not, with a real caller mistake quietly no longer
  caught. `is None` needs no such assumption.
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
- **`with_policy` shares the pool, and the pool is counted.** The copy holds the same client
  over the same `httpx` client, and a private `_Pool` counts its holders. Each guide releases
  once and the transport closes when the last one does, so closing a derived guide leaves its
  parent able to ask. Rust needs none of this: `Arc<Inner>` drops when the last clone does.
  Until 0.2.0 the count did not exist and `close()` on either guide closed both, which was
  survivable as a documented sharp edge and would have been a trap the moment `with` existed.
  The count alone is not enough, and the second half is that `share()` hands back a *new*
  client over the same pool, each carrying its own release-once flag. Returning `self` would
  give both guides one flag between them, so closing the first guide twice would spend the
  second guide's hold and shut the pool under it — the count cannot tell which holder a
  release came from. With a flag per client, closing one guide twice releases once. Sharing
  from an already-closed client is refused rather than copied: it would hand back a client
  holding nothing over a pool that may already be shut, and that only surfaces on the first
  ask. The clamp at zero inside `drop` is the last line of defence, not the mechanism.
  All of it — the count and every holder's flag — sits under one `threading.Lock` on the
  pool, because `Guide` tells you to share a guide across threads and a flag read, a flag
  flip and a decrement are three steps that must not interleave. The lock is taken when a
  guide is derived and when one is closed, never per request: `ask` reads the `httpx` client
  and nothing else. `httpx`'s own close happens after the lock is released, and no `await`
  is ever reached while it is held, so the asyncio pool uses the same plain lock.
  A patch that cannot settle is settled **before** the hold is taken. Building the derived
  guide in one expression took the hold first, because Python evaluates arguments left to
  right, and a bad patch then raised with nothing left to release it.
- **Some names mean two things, and the pairs are not renamed.** `Question`,
  `NoulQuestion`, `ChoiceQuestion` and `ScoreQuestion` each name a user-facing value in
  `guideme.question`, re-exported from `guideme`, and a pydantic wire model in `guideme.api`.
  The first is what a caller builds and annotates; the second is the shape it becomes on the
  way out. They never meet — nothing takes one where the other belongs, the wire models are
  built only inside `question_to_wire`, and the import graph is one-way — so the collision
  costs a reader one moment of "which one is this" that the import line answers, and renaming
  either side would cost more. The wire names have to mirror the API's `type` tags, and the
  user-facing ones are what a caller writes; a third spelling of either would be the one that
  had to be explained. `guideme.api.NoulCriteria` and `guideme.question.NoulCriteria` are a
  fifth such pair, for the same reason, and `api`'s module docstring already says so.
  `Usage` is the pair 0.2.0 added and the only one where **both** halves sit in a published
  `__all__`: `guideme.Usage` is the frozen dataclass a receipt carries and `guideme.api.Usage`
  is the pydantic model the response is parsed into, and `_receipt` copies one into the other.
  That is the cost of the decision above — not exporting the wire model is what creates a
  second `Usage` — and it is the right way round: a caller reaching the top-level surface gets
  the value object, and the name they would otherwise collide with is in a module they only
  import when they are building requests by hand.
- **State is JSON-shaped.** Anything `json.dumps` accepts without a default hook. A dataclass
  goes through `dataclasses.asdict`, a pydantic model through `.model_dump()`. This is the one
  untyped value in the package, and it is serialised at the boundary.
- **Fallback use is not on the answer event.** The event is emitted from the resolved outcome,
  before typed decoding chooses `.otherwise(…)` or the enum's fallback member. The settled
  thresholds are on the event, so "why unsure" is still answerable.
