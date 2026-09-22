# guideme

Judgments from [TypeSafe Jev](https://docs.typesafe.ai) that read like Python control flow.

A yes/no question is an `if`. A choice is an exhaustive `match` over your own enum. A score is
a comparison against your own ordered levels. Thresholds, unsure bands and fallbacks are
explicit and composable. Every request is one span. The decision logic is pure and its
contract is published under `spec/`, so every guideme SDK, in any language, answers the same
way.

```python
from guideme import Choice, Guide, Levels, choose, fallback, noul, score


class Department(Choice):
    billing = "Payments, invoicing, refunds"
    technical = "Bugs, outages, integrations"
    sales = fallback("Pricing, upgrades, new accounts")


class Frustration(Levels):
    calm = "Calm and polite"
    frustrated = "Frustrated"
    very_angry = "Very angry"


guide = Guide.from_env()

if guide.ask(noul("Should this ticket be escalated?"), ticket):
    escalate()

match guide.ask(choose(Department, "Which team should handle this?").min_confidence(0.6), ticket):
    case Department.billing:
        route_billing()
    case Department.technical:
        route_tech()
    case Department.sales:
        route_sales()

if guide.ask(score(Frustration, "How frustrated is the customer?"), ticket) >= (
    Frustration.frustrated
):
    prioritise()
```

The value of each member is the rubric the model reads. The member's name is the wire key. A
docstring on a member is documentation, not a rubric. pyright in strict mode enforces that
every option is handled, so adding a department turns the `match` above into an error until
you handle it. `from_env()` reads `TYPESAFE_API_KEY`, and `sales`, marked with `fallback(…)`,
is also the answer when confidence is below the floor. That floor is `min_confidence`, and it
is `0.0` unless you set it, which is why the choice above asks for `0.6`: with the default a
choice is never unsure and a `fallback(…)` member can never be reached.

Two members may not share a rubric: Python would make the second an alias of the first, so a
repeat is a `ConfigError` on the class statement rather than a rubric quietly one option
short. `fallback(…)` marks a `Choice` member and only a `Choice` member; a `Levels` is
ordered, so the level to fall back to when a score is unsure is `.otherwise(level)` on the
question.

That example is `tests/typing/readme.py`, which the gate type-checks with an `assert_type` on
every inferred answer type, so what is on this page cannot drift from what the package infers.
The three asks above are held to it by their use sites instead: the `match` is exhaustive and
the `>=` is between two levels of one scale.

## Install

```sh
uv add guideme
```

or

```sh
pip install guideme
```

Python 3.12 or newer. The package ships `py.typed`, so your checker sees every annotation.

Set `TYPESAFE_API_KEY` in the environment, or pass a key to
`Guide.builder().api_key(ApiKey("…")).build()`. Keys come from the TypeSafe console, on its
[keys page](https://console.typesafe.ai/keys).

guideme is not the official TypeSafe SDK. That one is
[`typesafe-sdk`](https://docs.typesafe.ai/sdk/python), which mirrors the API: you send
questions and read answers. guideme adds the layer above it, turning an answer into control
flow — your own enums as the option set, thresholds and an unsure ladder as policy, one span
per request — and talks to the API itself rather than wrapping that package.

## Three kinds of question, five constructors

| Constructor | Sends | Plain output | `.detail()` output |
|---|---|---|---|
| `noul("…")` | a yes/no question | `bool` | `Verdict` with the label and the probability |
| `choose(C, "…")` where `C` is a `Choice` | a choice over `C`'s 1 to 255 members | `C` | `Ranked[C]` with confidence and `probabilities`, the whole distribution |
| `score(L, "…")` where `L` is a `Levels` | a score over `L`'s 2 to 10 levels, low to high | `L`, the most probable level | `Scored[L]` with the expected `value`, the level, confidence and `distribution` |
| `choose_among("…", options)` | a choice over 1 to 255 runtime `{key: rubric}` pairs | `Key` | `Ranked[Key]` |
| `score_levels("…", levels)` | a score over 2 to 10 runtime level descriptions | `Rank` | `Scored[Rank]` |

Those size limits are the API's, and guideme checks them where you write the rubric: a `Choice`
or `Levels` class outside the range is a `ConfigError` on the class statement, and a runtime
rubric is one on the constructor call.

A noul can carry `.criteria("what yes means", "what no means")`. Instructions accept a string
or any JSON-shaped value, so a question can reference structured data by field name the way
the TypeSafe docs describe.

### Examples in a rubric

Two alternatives that read alike are told apart by showing inputs rather than by describing
harder. `option(…)` takes the inputs that belong to an alternative and the ones that belong
somewhere else, `level(…)` takes the inputs that score at that level, and `fallback(…)` is an
`option(…)` that also marks the unsure member. All three kinds of question take them: a noul's
`.criteria(…)` accepts an `option(…)` for the yes and for the no.

```python
from guideme import Choice, Levels, fallback, level, option


class Department(Choice):
    billing = option(
        "Payments, invoicing, refunds",
        examples=["My card was charged twice", "Where is my refund?"],
        counterexamples=["The dashboard is down"],
    )
    technical = option("Bugs, outages, integrations", examples=["502 on every request"])
    sales = fallback("Pricing, upgrades, new accounts", examples=["Do you have a team plan?"])


class Severity(Levels):
    cosmetic = level("No impact to functionality", examples=["typo in a label"])
    degraded = level("Broken feature, workaround exists", examples=["export fails in one browser"])
    blocking = level("No workaround exists", examples=["cannot log in", "data loss"])
```

The member's value is still the bare rubric; the examples are composed into it only in the
request, as

```text
Payments, invoicing, refunds
Examples: My card was charged twice; Where is my refund?
Not this option: The dashboard is down
```

So a rubric with no examples sends exactly what it sent before, and the same strings work in
`choose_among("…", {"billing": option(…)})` and `score_levels("…", [level(…), …])`. Examples
and counterexamples render in the order they are written, always: that order is part of the
published contract.

A yes and a no are two alternatives of one question, so they take examples too, and this is
where they pay best — a vague pair is the easiest thing to get wrong:

```python
urgent = noul("Is this ticket urgent?").criteria(
    option("Urgent", examples=["customers cannot log in", "money is moving to the wrong place"]),
    option("Not urgent", examples=["a broken job with a manual workaround", "a cosmetic bug"]),
)
```

Asked about a nightly export job that has been failing since Tuesday while the numbers are
pulled by hand, a plain `Urgent` / `Not urgent` answers yes at 0.75. The criteria above answer
no at 0.17, because one of the not-urgent examples is what the ticket describes.

A string may be an example of one option and a counterexample of another. That is the point
when two options are confusable, and it is the one overlap that stays legal. Offering the same
string as an example of two options, or as both an example and a counterexample of the same
option, says an input belongs where it cannot, so each is refused.

`level(…)` has no counterexamples, because "not this option" means nothing on an ordered
scale — an input that does not belong at one level scores at another.

Leave a clause out to say there is none. An empty one written out — `examples=[]` or
`examples=()` — says nothing, so it is refused as the mistake it is, along with a clause given
as one string rather than a list of them (`examples="refund"` would otherwise be six one-letter
examples), a blank entry, a repeat within one clause, a newline or carriage return inside an
entry, a counterexample on a level, a `fallback(…)` given to `choose_among`, `score_levels` or
`.criteria(…)`, and the two contradictions above. Each is a `ConfigError` where the rubric is
written.

Entries go on one line each, so a newline inside one would read as a clause you never wrote.
`"; "` inside an entry is fine — `"card declined; retry failed"` is ordinary prose, and it
changes how many examples a reader sees rather than which clause they are in. The rubric text
itself may still contain newlines; only the entries are restricted.

Attaching examples to a blank rubric is refused too, because they describe something that is not
there. A blank rubric on its own is not: it means what it has always meant, and adding examples
to the language does not make an old declaration an error.

The state is anything JSON-shaped: a text literal, a `dict`, a list of them. A dataclass goes
through `dataclasses.asdict`, a pydantic model through `.model_dump()`.

## Policy

Thresholds decide how a probability or a confidence becomes an answer. They form a patch that
merges from the question, over the guide, over the package defaults. A `Policy` is that patch,
with every field optional; settling one against the defaults gives a `Thresholds`, which is
what `guideme.policy.resolve` takes and what every golden vector is written against.

| Layer | How to set | Wins over |
|---|---|---|
| question | `.yes_above(p)`, `.no_below(p)` on nouls; `.min_confidence(c)` on choice and score; `.with_policy(Policy(…))` on any | guide |
| guide | `Guide.builder().policy(…)`, or `guide.with_policy(…)` for a scoped copy | defaults |
| defaults | `yes_above 0.5`, `no_below 0.5`, `min_confidence 0.0` | nothing |

The rules:

- Noul: `p >= yes_above` is yes, `p <= no_below` is no, strictly between is unsure. With the
  defaults there is no unsure band.
- Choice and score: `confidence < min_confidence` is unsure. With the default there is never
  an unsure answer.

When an answer is unsure, resolution goes down a ladder: `.otherwise(value)` on the question,
then the enum's `fallback(…)` member, then `UnsureError` naming the question and the boundary
it missed. `.detail()` skips the ladder and hands you the reading to decide yourself.

`Policy` is a frozen dataclass, so a house policy is a module constant:

```python
CAUTIOUS = Policy(yes_above=0.7, no_below=0.3)

guide = Guide.builder().api_key(key).policy(CAUTIOUS).build()
strict = guide.with_policy(Policy(min_confidence=0.8))

reading = guide.ask(noul("Is this about billing?").detail(), ticket)
match reading.verdict:
    case "yes":
        billing()
    case "no":
        other()
    case "unsure":
        review(reading.p)

picked = strict.ask(choose(Department, "Which team?").detail(), ticket)
```

`strict` shares the connection pool and inherits `CAUTIOUS`, with `min_confidence` patched over
it, so the choice above is unsure below `0.8` while the noul still reads against `0.7 / 0.3`.

## Several judgments, one request

A tuple of questions is a question. So is a list or a dict, and they nest. The answer has the
same shape, from one request and one span. Each question keeps its own policy.

```python
urgent, dept, mood, flags = guide.ask(
    (
        noul("Is this urgent?").yes_above(0.7).no_below(0.3).otherwise(False),
        choose(Department, "Which team?").min_confidence(0.6),
        score(Frustration, "How frustrated?").detail(),
        {"spam": noul("Is it spam?"), "vip": noul("Is the sender a VIP?")},
    ),
    ticket,
)
# pyright infers tuple[bool, Department, Scored[Frustration], dict[str, bool]]

if urgent or mood.value > 1.5 or flags["vip"]:
    prioritise()
```

Question ids are `q0..qN` in encounter order, which for a dict is insertion order. They appear
on the wire, in errors and in events. A batch is atomic: one answer that cannot be resolved
fails the whole call, so put `.otherwise(…)` or `.detail()` on the questions that may come
back unsure.

Any nesting works at runtime. The forms your checker infers a type for are a single question,
a list, a dict, a tuple of up to eight questions, and a tuple of up to seven followed by one
list or dict, which is the shape above.

## Sync and async

`Guide` and `AsyncGuide` have the same surface over the same pure core. The difference is the
`await` and the `httpx` client underneath.

```python
async with AsyncGuide.from_env() as guide:
    verdict: Verdict = await guide.ask(noul("Is this about billing?").detail(), ticket)
```

The synchronous version is the same two lines with `Guide`, `with` and no `await`.
`Guide.builder()` and `AsyncGuide.builder()` return the same `GuideBuilder`; `.build()` gives
the synchronous guide and `.build_async()` the asynchronous one. Leaving the block closes the
guide, and `guide.close()` does the same thing by hand for a guide that outlives any block.

A guide holds a connection pool, so build one and share it: both kinds are safe to use from
several threads or several tasks at once, and one guide asking concurrently is what the pool
is for. Building one per request works but opens a pool per request, which is the cost the
pool exists to avoid. `guide.with_policy(…)` returns a second guide over the *same* pool, and
the two are counted, so closing either leaves the other able to ask and the pool closes when
the last of them does. Close each guide once.

Both guides also answer `models()`, which returns a `tuple[ModelInfo, ...]`: the models the
account may use, each with its `name`, `description` and `release_date`. It is one call to
`GET /v1/models` and gets no ask span of its own. It is retried on exactly the terms an ask
is, so a `429` while your process is starting up does not fail the start.

## What a request cost

`ask_with_receipt` is `ask` with the response's own numbers kept. It takes the same shapes and
infers the same types; `ask` is this call followed by `.answer`.

```python
receipt: Receipt[bool] = guide.ask_with_receipt(noul("Is this urgent?"), ticket)

if receipt.answer:
    prioritise()
meter(model=receipt.model, tokens=receipt.usage.input_tokens)
```

`Receipt` is frozen and carries three things: `answer`, whatever `ask` would have returned;
`model`, the versioned id that actually answered, which is `jev-1.13.0` and not `jev-latest`
even when an alias was asked for; and `usage`, a `Usage` with `input_tokens` and
`output_tokens`. Input tokens are what is billed. Log the model: thresholds are tuned against
one model's numbers, and the alias moves under you.

## Configuration

Every setter on `GuideBuilder` returns the builder, and `Guide.builder()` starts one.

| Setter | Default | What it does |
|---|---|---|
| `api_key(ApiKey(…))` | none; required | The key. `from_env()` reads it from `TYPESAFE_API_KEY`. |
| `base_url(…)` | `https://api.typesafe.ai` | The API origin. It may not carry credentials. |
| `model(Model(…))` | `jev-latest` | The model or alias to ask. |
| `policy(Policy(…))` | the defaults | The guide-wide policy patch; a question's own wins over it. |
| `max_retries(n)` | `3` | Resends per call, `0` to never resend. |
| `backoff(…)` | 500 ms | Base of the exponential backoff. |
| `timeout(…)` | 30 s | Per phase of one attempt. Read the paragraph below. |
| `transport(…)` / `async_transport(…)` | `httpx`'s own | Send through your `httpx` transport. |
| `record_state(True)` | off | Put the state JSON on the ask span. It is your users' data. |
| `events(…)` | `"both"` | Whether an answer and a retry go to the span, a log record, or both. |

**`timeout` is not a deadline for the attempt.** `httpx` gives the whole budget to each phase
separately — connecting, writing, reading, and waiting for a pooled connection — so one attempt
that is slow in more than one phase takes longer than the timeout without breaching anything.
Worst case for a call is `max_retries + 1` attempts of several phases each, plus the backoff
between them. The Rust SDK's `reqwest` deadline covers the attempt as a whole instead; the two
differ because their HTTP clients do, and `docs/contract.md` records it as a divergence rather
than leaving you to find it.

`transport(…)` and `timeout(…)` refuse each other, in whichever order you write them: a
timeout belongs to the transport that honours it, and `httpx` hands yours this budget as a
request extension it is free to ignore. A silent no-op would be worse than a `ConfigError`.
`transport(…)` is for `build()` and `async_transport(…)` for `build_async()`; using one with
the other build is a `ConfigError` too.

## Testing your code

Pass a transport and your control flow is testable with no server, no port and no key:

```python
import httpx
from guideme import ApiKey, Guide, GuideBuilder, noul


def answer(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "jev-1.13.0",
            "answers": {"q0": {"type": "noul", "noul": 0.95}},
            "usage": {"input_tokens": 296, "output_tokens": 20},
        },
    )


def test_an_urgent_ticket_is_prioritised() -> None:
    builder = GuideBuilder().api_key(ApiKey("not-a-real-key"))
    with builder.transport(httpx.MockTransport(answer)).build() as guide:
        assert guide.ask(noul("Is this urgent?"), "payouts failing") is True
```

`q0` is the first question in encounter order; a batch of three is answered with `q0`, `q1`
and `q2`. Raise from the handler instead of returning and you get the failure paths: an
`httpx.ConnectError` is resent inside the retry budget, and an `httpx.ReadTimeout`, an
`httpx.ConnectTimeout` or an `httpx.RemoteProtocolError` is not. For
`AsyncGuide`, hand the same `httpx.MockTransport` to `async_transport(…)` and `build_async()`;
it is both kinds of transport at once.

## Observability

guideme emits OpenTelemetry spans, span events and OTLP log records through
`opentelemetry-api` and installs nothing: no tracer provider, no logger provider, no exporter,
no logging handler. Install a provider and the data appears. The SDK and an exporter are not
dependencies of this package, so install them alongside it:

```sh
pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc
```

The smallest provider that leaves the process:

```python
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(provider)
```

One span named `guideme.ask` per request, shaped by the OpenTelemetry GenAI conventions:
`gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.usage.*`, and on failure `error.type`
with an error status. Under it, one HTTP client span per attempt with
`http.response.status_code`, so a retry is visible as sibling spans, plus a `guideme.retry`
event when an attempt is throttled. One `guideme.answer` event per question with the outcome,
the probability or confidence, the unsure verdict and the settled thresholds that produced it.
The state is never recorded unless you opt in with `record_state(True)`. The API key never
appears anywhere.

Every answer and every retry is also an OTLP log record, at `INFO` and at `WARN`, carrying the
trace id and the span id of the span it came from, so a logs backend links one straight back to
the decision it explains. Install a `LoggerProvider` too and they arrive; install neither and
they cost nothing. `events(...)` on the builder picks which signal carries an event when you
export both; **Choosing a signal** in the observability document has the table, the default, and
what happens on an `opentelemetry-api` that has no logs API: asking for one is refused, and the
default falls back to the span event alone.

Because the shapes are standard, any OTLP backend reads them as is.
[`docs/observability.md`](https://github.com/pedro-pscunha/guideme-python/blob/main/docs/observability.md)
has the field tables and the environment variables that point the exporter anywhere.
[`examples/otlp`](https://github.com/pedro-pscunha/guideme-python/tree/main/examples/otlp) runs
all of it against the live API with a collector that prints what arrives.

## Errors

Every failure is a `GuidemeError`. `.kind` is the same string every other guideme SDK reports
and the value of `error.type` on the failed span.

| Class | `.kind` | When |
|---|---|---|
| `AuthError` | `auth` | 401 |
| `InvalidError` | `invalid` | 422; `.detail` is the body |
| `RateLimitedError` | `rate_limited` | 429 after retries, or a `retry-after` too long to wait for; `.retry_after` carries it |
| `OverloadedError` | `overloaded` | 529 on the same terms; `.retry_after` carries it too |
| `TransportError` | `transport` | connection, TLS, timeout |
| `UnexpectedStatusError` | `unexpected_status` | anything the contract does not define |
| `ProtocolError` | `protocol` | the response violates the contract: undecodable body, wrong answer kind, option or level not in the rubric, probability outside 0..1 |
| `UnsureError` | `unsure` | the policy said unsure and nothing caught it |
| `ConfigError` | `config` | raised where the mistake is written: bad thresholds, missing key, empty batch, unserialisable state, a duplicate rubric, a rubric outside 1..255 options or 2..10 levels, a bad `events(...)`, a non-positive timeout, negative retries or backoff, a `base_url` carrying credentials, and so on |

Retries on 429 and 529 use exponential backoff with jitter, capped at 30 s, and honour an
integer `retry-after`. They apply to `models()` as much as to `ask`.

A failed connection is resent in the same budget: refused, reset, or a TLS handshake that did
not complete. The request never reached a server, so nothing was judged and nothing is
repeated. A disconnect part-way through a response is **not** resent — the request arrived,
the API may have answered it, and asking again would buy the same judgment twice.

**No timeout is resent, of any phase.** A connect timeout included, although `httpx` names it
separately: Rust's SDK sets one deadline over the whole attempt and cannot tell a connect
timeout from a read timeout, so retrying one here would make the two SDKs disagree about the
same failure, and a retried timeout multiplies the wall time `timeout(…)` is there to bound.
Everything not resent raises `TransportError` on the first failure.

## Lower layers

Everything above is re-exported from the `guideme` package, and `guideme.__all__` is that list.
The three modules below are a second supported tier: you import them by their own path, they
are not re-exported at the top level, and they are under the same rule as the first tier —
nothing in them is removed or renamed without a major version and a `CHANGELOG.md` entry.
Anything else in the package is private, whatever its name looks like.

- `guideme.api` is the exact wire mirror of `POST /v1/systemone` and `GET /v1/models`, and
  `guideme.api.__all__` is what it offers: the request and response models, `Usage`, and the
  four adapters between them and the core. `guideme.api.client` holds `Client` and
  `AsyncClient` for callers who want to build requests themselves. They live one level down
  rather than on `guideme.api` because re-exporting them would make `api` and `api.client`
  import each other, and the gate fails an import cycle.
- `guideme.question` is where the question types are declared, and all of them are re-exported
  above: `Question` is what `noul`, `choose`, `choose_among`, `score` and `score_levels`
  return, and `NoulQuestion`, `ChoiceQuestion`, `ScoreQuestion`, `DetailedNoul`,
  `DetailedChoice` and `DetailedScore` are the concrete ones. Inference covers most uses, so
  reach for them when you need to annotate a question you are storing or passing on: a `dict`
  is invariant, so a `dict[str, NoulQuestion]` is not a `dict[str, Question[bool]]` and the
  annotation has to be written. Everything else in `guideme.question` is private.
- The scalars are validated once and never re-checked: `Probability` and `Confidence` hold the
  unit-interval numbers on `Verdict`, `Ranked` and `Scored`, `Key` and `Rank` are what a runtime
  rubric answers with, `Model` names the model to ask, and `ApiKey` carries the key without ever
  printing it. The first four are `NewType` brands, so the guarantee is that only the wire mints
  them, not that `Probability(2.0)` is rejected; it is not.
- `guideme.policy.resolve(answer, thresholds)` is the pure decision function. `spec/` holds
  its JSON Schemas and 42 golden vectors, vendored from
  [guideme-rust](https://github.com/pedro-pscunha/guideme-rust), which publishes the contract.
  [`docs/contract.md`](https://github.com/pedro-pscunha/guideme-python/blob/main/docs/contract.md)
  says what every guideme SDK must satisfy and
  [`docs/design.md`](https://github.com/pedro-pscunha/guideme-python/blob/main/docs/design.md)
  records the design and its sharp edges.

## Other SDKs

Every guideme SDK is written from scratch in its own language and answers the same way,
because they all satisfy one contract: the wire schemas, the 42 golden policy vectors and the
interface shape that [guideme-rust](https://github.com/pedro-pscunha/guideme-rust) publishes
under `spec/` and states in
[`docs/contract.md`](https://github.com/pedro-pscunha/guideme-rust/blob/main/docs/contract.md).

| Language | Package | Repository |
|---|---|---|
| Python | `guideme` | this repository |
| Rust | [`guideme`](https://crates.io/crates/guideme) | [guideme-rust](https://github.com/pedro-pscunha/guideme-rust) |

This repository vendors that `spec/` and records the commit it came from in `spec/SOURCE`; a
CI job fails when the copy drifts from the Rust repository's `main`. The span, event and
attribute names are shared too, so one dashboard reads both SDKs.

## Environment

| Variable | Meaning |
|---|---|
| `TYPESAFE_API_KEY` | required by `Guide.from_env()` and `AsyncGuide.from_env()` |
| `TYPESAFE_BASE_URL` | optional API origin override |
| `GUIDEME_MODEL` | optional model or alias; default `jev-latest` |

## Development

Tooling is managed by [mise](https://mise.jdx.dev), which pins `uv` and `gitleaks`; `uv` pins
everything else from `pyproject.toml` and `uv.lock`.

```
mise install      # fetch the tools
mise run sync     # install the locked environment
mise run check    # fmt-check, gen-check, ruff, pyright, pylint, pytest, build, audit
mise run test     # pytest alone
mise run hooks    # point core.hooksPath at the tracked hooks in .githooks
```

The hooks are tracked, not generated: `mise run hooks` sets this repository's `core.hooksPath`
to `.githooks` and verifies it took effect.
[`AGENTS.md`](https://github.com/pedro-pscunha/guideme-python/blob/main/AGENTS.md) says what
each stage runs.

Library code is held to a strict checker set: `pyright` in strict mode, `ruff` with every rule
selected, and `pylint` with every check enabled. Tests are few and high-grade: property tests
for the policy laws, a real local HTTP server for the wire and retry contract, structural
tracing assertions, pyright files that must fail, and a drift guard that re-resolves every
golden vector.

From a source distribution rather than a clone, `mise.toml` and `uv.lock` are not present, so
the suite runs with `uv run --group dev pytest`.

Two opt-in tests hit the real API and are deselected by default:

```
TYPESAFE_API_KEY=… uv run --locked pytest -m live
```

Contributor rules live in
[`AGENTS.md`](https://github.com/pedro-pscunha/guideme-python/blob/main/AGENTS.md). Report a
vulnerability privately, as
[`SECURITY.md`](https://github.com/pedro-pscunha/guideme-python/blob/main/SECURITY.md)
describes, never in a public issue.

## License

MIT or Apache-2.0, at your option.
