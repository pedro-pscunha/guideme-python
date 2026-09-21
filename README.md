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


guide = Guide.from_env()  # reads TYPESAFE_API_KEY

if guide.ask(noul("Should this ticket be escalated?"), ticket):
    escalate()

match guide.ask(choose(Department, "Which team should handle this?"), ticket):
    case Department.billing:
        route_billing()
    case Department.technical:
        route_tech()
    case Department.sales:
        route_sales()  # also the answer when confidence is below the floor

mood = guide.ask(score(Frustration, "How frustrated is the customer?"), ticket)
if mood >= Frustration.frustrated:
    prioritise()
```

The value of each member is the rubric the model reads. The member's name is the wire key. A
docstring on a member is documentation, not a rubric. pyright in strict mode enforces that
every option is handled.

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
`Guide.builder().api_key(ApiKey("…"))`.

## The three questions

| Constructor | Sends | Plain output | `.detail()` output |
|---|---|---|---|
| `noul("…")` | a yes/no question | `bool` | `Verdict` with the label and the probability |
| `choose(C, "…")` where `C` is a `Choice` | a choice over `C`'s members | `C` | `Ranked[C]` with confidence and the full distribution |
| `score(L, "…")` where `L` is a `Levels` | a score over `L`'s levels, low to high | `L`, the most probable level | `Scored[L]` with the expected `value`, the level, confidence and distribution |
| `choose_among("…", options)` | a choice over runtime `{key: rubric}` pairs | `Key` | `Ranked[Key]` |
| `score_levels("…", levels)` | a score over runtime level descriptions | `Rank` | `Scored[Rank]` |

A noul can carry `.criteria("what yes means", "what no means")`. Instructions accept a string
or any JSON-shaped value, so a question can reference structured data by field name the way
the TypeSafe docs describe.

The state is anything JSON-shaped: a text literal, a `dict`, a list of them. A dataclass goes
through `dataclasses.asdict`, a pydantic model through `.model_dump()`.

## Policy

Thresholds decide how a probability or a confidence becomes an answer. They form a patch that
merges from the question, over the guide, over the package defaults.

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
```

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
guide = Guide.from_env()
answer = guide.ask(noul("Is this urgent?"), ticket)

aguide = AsyncGuide.from_env()
answer = await aguide.ask(noul("Is this urgent?"), ticket)
```

`Guide.builder()` and `AsyncGuide.builder()` return the same `GuideBuilder`; `.build()` gives
the synchronous guide and `.build_async()` the asynchronous one.

## Observability

guideme emits OpenTelemetry spans and events through `opentelemetry-api` and installs nothing:
no tracer provider, no exporter, no logging handler. Install a provider and the spans appear.
The smallest one that leaves the process:

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

Because the shapes are standard, any OTLP backend reads them as is.
[`docs/observability.md`](docs/observability.md) has the field tables and the environment
variables that point the exporter anywhere. [`examples/otlp`](examples/otlp) runs all of it
against the live API with a collector that prints what arrives.

## Errors

Every failure is a `GuidemeError`. `.kind` is the same string every other guideme SDK reports
and the value of `error.type` on the failed span.

| Class | `.kind` | When |
|---|---|---|
| `AuthError` | `auth` | 401 |
| `InvalidError` | `invalid` | 422; `.detail` is the body |
| `RateLimitedError` | `rate_limited` | 429 after retries, or a `retry-after` too long to wait for |
| `OverloadedError` | `overloaded` | 529 after retries |
| `TransportError` | `transport` | connection, TLS, timeout |
| `UnexpectedStatusError` | `unexpected_status` | anything the contract does not define |
| `ProtocolError` | `protocol` | the response violates the contract: undecodable body, wrong answer kind, option or level not in the rubric, probability outside 0..1 |
| `UnsureError` | `unsure` | the policy said unsure and nothing caught it |
| `ConfigError` | `config` | bad thresholds, missing key, empty batch, unserialisable state, empty or duplicate rubric |

Retries on 429 and 529 use exponential backoff with jitter, capped at 30 s, and honour
`retry-after`.

## Lower layers

- `guideme.api` is the exact wire mirror of `POST /v1/systemone` and `GET /v1/models`, plus
  `Client` and `AsyncClient` for callers who want to build requests themselves.
- `guideme.policy.resolve(answer, thresholds)` is the pure decision function. `spec/` holds
  its JSON Schemas and 42 golden vectors, vendored from
  [guideme-rust](https://github.com/pedro-pscunha/guideme-rust), which publishes the contract.
  [`docs/contract.md`](docs/contract.md) says what every guideme SDK must satisfy and
  [`docs/design.md`](docs/design.md) records the design and its sharp edges.

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
to `.githooks` and verifies it took effect. [`AGENTS.md`](AGENTS.md) says what each stage runs.

Library code is held to a strict checker set: `pyright` in strict mode, `ruff` with every rule
selected, and `pylint` with every check enabled. Tests are few and high-grade: property tests
for the policy laws, a real local HTTP server for the wire and retry contract, structural
tracing assertions, pyright files that must fail, and a drift guard that re-resolves every
golden vector.

Two opt-in tests hit the real API and are deselected by default:

```
TYPESAFE_API_KEY=… uv run --locked pytest -m live
```

Contributor rules live in [`AGENTS.md`](AGENTS.md). Report a vulnerability privately, as
[`SECURITY.md`](SECURITY.md) describes, never in a public issue.

## License

MIT or Apache-2.0, at your option.
