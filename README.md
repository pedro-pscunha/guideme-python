# guideme (Python)

[![PyPI](https://img.shields.io/pypi/v/guideme.svg)](https://pypi.org/project/guideme/)
[![Python](https://img.shields.io/pypi/pyversions/guideme.svg)](https://pypi.org/project/guideme/)
[![license](https://img.shields.io/pypi/l/guideme.svg)](https://github.com/pedro-pscunha/guideme-python#license)

guideme sends a question and your state to [TypeSafe Jev](https://docs.typesafe.ai), the
TypeSafe model that gives judgments. It gives back the answer as a normal Python value: a
`bool`, a member of your own enum, or one of your own ordered levels. Your code then acts on the
answer with an `if`, a `match` or a comparison. Unlike the official
[`typesafe-sdk`](https://docs.typesafe.ai/sdk/python), which mirrors the API, guideme turns an
answer into control flow and calls the API itself.

## Install

```sh
uv add guideme        # or: pip install guideme
```

guideme needs Python 3.12 or newer. The package ships `py.typed`, so your type checker sees
every annotation. Get an API key on the [keys page](https://console.typesafe.ai/keys) of the
TypeSafe console. Set it in the environment as `TYPESAFE_API_KEY`. To give the key in code, use
`Guide.builder().api_key(ApiKey("…")).build()`.

## Quick start

This example asks three questions about one support ticket.

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

- `ticket` is the *state*: the data that you send with the question, here a support ticket.
  `escalate()` and the `route_…()` functions are your own code.
- `noul(…)` asks a yes/no question (TypeSafe calls it a *noul*). `choose(…)` asks a choice, and
  `score(…)` asks a score.
- `Guide.from_env()` reads the key from `TYPESAFE_API_KEY`.
- The value of a member is its *rubric*: the text that tells the model what the option or level
  means. The name of the member is its key on the wire. A docstring on a member is not rubric.
- Declare the levels from low to high. The first member is the lowest level, and `>=` compares
  by this order.
- pyright in strict mode makes sure that the `match` handles every option. If you add a
  department, the `match` is an error until you handle it.
- `sales` is the fallback: the answer when the choice is *unsure*, that is, when its confidence
  is less than `min_confidence`. The default `min_confidence` is `0.0`, so a choice is never
  unsure and `sales` is never used as the fallback. That is why this choice sets `0.6`.

Build one guide and share it: the Configuration section tells why.

## Questions

There are three kinds of question. A yes/no question gives a `bool`. A choice gives one of your
options. A score gives one level of your ordered scale. Each constructor below gives the plain
answer. Add `.detail()` to a question to get the full reading in its place: the probabilities,
the confidence and the `unsure` flag.

| Constructor | Asks | Plain answer | `.detail()` answer |
|---|---|---|---|
| `noul("…")` | a yes/no question | `bool` | `Verdict`: `verdict` (`"yes"`, `"no"`, `"unsure"`), `p` |
| `choose(C, "…")`, `C` a `Choice` | a choice over 1 to 255 options | a member of `C` | `Ranked[C]`: `choice`, `confidence`, `unsure`, `probabilities` |
| `score(L, "…")`, `L` a `Levels` | a score over 2 to 10 levels, low to high | the most probable member of `L` | `Scored[L]`: `value`, `level`, `confidence`, `unsure`, `distribution` |
| `choose_among("…", options)` | a choice over 1 to 255 `{key: rubric}` pairs given at runtime | `Key`, the key | `Ranked[Key]` |
| `score_levels("…", levels)` | a score over 2 to 10 level texts given at runtime | `Rank`, the index from 0 | `Scored[Rank]` |

The `value` of a score is the probability-weighted level number. The lowest level is 0, and the
value can land between two levels.

```python
team = guide.ask(choose_among("Which team?", {"billing": "Payments", "technical": "Bugs"}), ticket)
rank = guide.ask(score_levels("How severe?", ["Cosmetic", "Degraded", "Blocking"]), ticket)
```

The size limits come from the TypeSafe API. A `Choice` or `Levels` class out of range raises
`ConfigError` on the class statement, and a runtime rubric raises it on the constructor call.
Two members of one class cannot have the same rubric: Python makes the second an alias of the
first, so that is a `ConfigError` too.

The question text (the `instructions` argument) can be a string or any JSON-shaped value, so it
can name fields of structured state. A yes/no question can also say what yes and no mean with
`.criteria("what yes means", "what no means")`.

The state can be anything JSON-shaped: a string, a number, a `dict`, a list, and nestings of
them. Convert a dataclass with `dataclasses.asdict` and a pydantic model with `.model_dump()`.
A value that `json` cannot write, such as `NaN` or `bytes`, raises `ConfigError` before anything
is sent.

`guide.models()` returns a `tuple[ModelInfo, ...]`: the models that your account can use, each
with `name`, `description` and `release_date`. It is one `GET /v1/models` call, with no ask
span. It is retried like an ask, so a `429` while your process starts does not stop the start.

## When the model is not sure

An answer is *unsure* when it is not certain enough under the thresholds:

- Yes/no question: `p >= yes_above` is yes, `p <= no_below` is no, and between the two is
  unsure. The defaults are `0.5` and `0.5`, so no answer is unsure.
- Choice and score: `confidence < min_confidence` is unsure. With the default `0.0`, no answer
  is unsure.

A `Policy` is a set of thresholds, and each field is optional. You can set it at two layers:

- On a question: `.yes_above(p)` and `.no_below(p)` on a yes/no question, `.min_confidence(c)`
  on a choice or a score, and `.with_policy(Policy(…))` on any question.
- On a guide: `Guide.builder().policy(…)`, or `guide.with_policy(…)` for a copy.

The question wins over the guide, and the guide wins over the defaults. When an answer is unsure,
guideme goes down the *unsure ladder*:

1. The `.otherwise(value)` of the question.
2. The `fallback(…)` member of the `Choice`. A `Levels` class has no fallback member, so for a
   score use `.otherwise(level)`.
3. `UnsureError`, which names the question and the threshold that it missed.

`.detail()` skips the ladder and drops any `.otherwise(…)`. It never raises `UnsureError` and
gives you the full reading to decide yourself.

`Policy` is a frozen dataclass, so you can keep one in a module constant:

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

`strict` shares the connection pool of `guide` and keeps `CAUTIOUS`, with `min_confidence` set
over it. Thus the choice is unsure below `0.8`, and the yes/no question still uses `0.7 / 0.3`.

## Examples and counterexamples

Two options that read alike are easier to tell apart with inputs than with a longer rubric. An
*example* is an input that belongs to an option. A *counterexample* is an input that does not.
`option(…)` takes both. `level(…)` takes examples only, because on an ordered scale an input
that does not belong at one level belongs at another. `fallback(…)` is an `option(…)` that also
marks the fallback member.

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

The value of the member stays the bare rubric. guideme adds the examples only in the request,
as this text:

```text
Payments, invoicing, refunds
Examples: My card was charged twice; Where is my refund?
Not this option: The dashboard is down
```

A rubric with no examples sends only its text. Examples render in the order that you write them,
and this text is part of the published contract. The same values work at runtime, in
`choose_among("…", {"billing": option(…)})` and `score_levels("…", [level(…), …])`.

`.criteria(…)` takes an `option(…)` for the yes and one for the no. A vague yes/no pair is easy
to get wrong, and examples help most there:

```python
urgent = noul("Is this ticket urgent?").criteria(
    option("Urgent", examples=["customers cannot log in", "money is moving to the wrong place"]),
    option("Not urgent", examples=["a broken job with a manual workaround", "a cosmetic bug"]),
)
```

[`docs/design.md`](https://github.com/pedro-pscunha/guideme-python/blob/main/docs/design.md#decisions)
records a measured case where these examples change a wrong yes into a correct no.

A rubric obeys these rules. A rubric that breaks one raises `ConfigError` where you write it:

- Leave a clause out to say there are none. An empty clause, such as `examples=[]`, is refused.
- Give a clause as a list, not as one string such as `examples="refund"`.
- An entry is not blank, is on one line (no newline or carriage return), and appears once in its
  clause. An entry can contain `"; "`.
- One string cannot be an example of two options (or of the yes and the no, or of two levels).
- One string cannot be an example and a counterexample of the same option. It can be an example
  of one option and a counterexample of another: that is how you tell two similar options apart.
- A level has no counterexamples, and `choose_among`, `score_levels` and `.criteria(…)` take no
  `fallback(…)`.
- Examples or counterexamples on a blank rubric are refused. A blank rubric alone is legal, and
  the rubric text itself can contain newlines.

## Several questions in one request

`ask` also takes a tuple, a list or a dict of questions, and they nest. The answer has the same
shape and comes from one request and one span. Each question keeps its own policy.

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

The question ids are `q0..qN` in encounter order (insertion order for a dict). They appear on
the wire, in errors and in events. A batch is atomic: if one answer cannot be resolved, the
whole call fails. So put `.otherwise(…)` or `.detail()` on each question that can come back
unsure.

Any nesting works at runtime. Your type checker infers a type for one question, a list and a
dict. It also infers a tuple of up to eight questions, or of up to seven followed by one list or
dict (the shape above).

## The receipt

`ask_with_receipt` is `ask` that also keeps the numbers of the response. It takes the same
shapes and infers the same types. `ask` is `ask_with_receipt` followed by `.answer`.

```python
receipt: Receipt[bool] = guide.ask_with_receipt(noul("Is this urgent?"), ticket)

if receipt.answer:
    prioritise()
meter(model=receipt.model, tokens=receipt.usage.input_tokens)
```

A `Receipt` is frozen. `answer` is what `ask` returns. `model` is the versioned id of the model
that answered, for example `jev-1.13.0`, also when you asked for the `jev-latest` alias. `usage`
is a `Usage` with `input_tokens` and `output_tokens`, and TypeSafe bills the input tokens. Log
the model: thresholds are tuned against the numbers of one model, and an alias can move.

## Errors

Every failure is a `GuidemeError`. Its `.kind` is the error kind: the same string in every
guideme SDK, and the value of `error.type` on the failed span.

| Class | `.kind` | When |
|---|---|---|
| `AuthError` | `auth` | HTTP 401 |
| `InvalidError` | `invalid` | HTTP 422. `.detail` is the response body. |
| `RateLimitedError` | `rate_limited` | HTTP 429 after the retries, or a `retry-after` that is too long to wait. `.retry_after` holds it. |
| `OverloadedError` | `overloaded` | HTTP 529, on the same terms. `.retry_after` holds it. |
| `TransportError` | `transport` | A connection, TLS or timeout failure, or a disconnect during the response. |
| `UnexpectedStatusError` | `unexpected_status` | A status that the contract does not define. |
| `ProtocolError` | `protocol` | The response breaks the contract: a body that does not decode, a wrong answer kind, an option or level that is not in the rubric, a probability outside 0..1. |
| `UnsureError` | `unsure` | The policy said unsure and the ladder had no value. |
| `ConfigError` | `config` | A mistake in your code, raised before anything is sent. For example: bad thresholds, no key, an empty batch, state or instructions that `json` cannot write, a rubric that breaks a rule, two fallback members in one `Choice`, a bad `events(…)`, a timeout that is not positive, negative retries or backoff, a malformed `base_url` or one that holds credentials, a timeout next to a transport, a transport given to the wrong build, `with_policy(…)` on a closed guide. |

## Retries and timeouts

guideme retries HTTP 429 and 529, for an ask and for `models()`. The backoff is exponential with
jitter, capped at 30 s. guideme obeys a `retry-after` header in whole seconds. If `retry-after`
is more than 30 s, guideme does not wait: it raises `RateLimitedError` or `OverloadedError`.

A failed connection is resent in the same budget: a refused or reset connection, or a TLS
handshake that did not complete. That request did not reach a server, so nothing was answered.
A call makes at most `max_retries + 1` attempts, whatever the mix of failures.

A disconnect during the response is not resent: the API can have answered, and a second request
pays for the same answer twice. guideme does not resend a timeout, in any phase. This includes a
connect timeout. Each of these raises `TransportError` at once.
[`docs/design.md`](https://github.com/pedro-pscunha/guideme-python/blob/main/docs/design.md#decisions)
gives the reasons.

**`timeout` is not a deadline for the attempt.** `httpx` gives the full value to each phase:
connect, write, read, and the wait for a pooled connection. Thus one slow attempt can take more
than the timeout. The worst case for a call is `max_retries + 1` attempts of several phases each,
plus the backoff between them. The Rust SDK differs here, as
[`docs/contract.md`](https://github.com/pedro-pscunha/guideme-python/blob/main/docs/contract.md)
records.

## Testing your code

Give the guide an `httpx` transport. Then you can test your control flow with no server, no port
and no key:

```python
import httpx
from guideme import ApiKey, Guide, noul


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
    builder = Guide.builder().api_key(ApiKey("not-a-real-key"))
    with builder.transport(httpx.MockTransport(answer)).build() as guide:
        assert guide.ask(noul("Is this urgent?"), "payouts failing") is True
```

`q0` is the first question in encounter order, and a batch of three uses `q0`, `q1` and `q2`.
To test a failure, raise an `httpx` exception in the handler. The Retries and timeouts section
says which ones guideme resends. For `AsyncGuide`, give the same `httpx.MockTransport` to
`async_transport(…)` and call `build_async()`. A `MockTransport` is both kinds of transport.

## Observability

guideme sends OpenTelemetry spans, span events and OTLP log records through `opentelemetry-api`.
It installs no provider, no exporter and no logging handler. When you install a provider, the
data appears. Install the SDK and an exporter next to guideme
(`pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc`), then:

```python
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(provider)
```

Each `ask` is one `guideme.ask` span with the OpenTelemetry GenAI fields, and one HTTP client
span per attempt below it. A retry adds a `guideme.retry` event. Each question adds a
`guideme.answer` event with the outcome, the probability or confidence and the thresholds. Each
answer is also an OTLP log record at `INFO`, and each retry one at `WARN`, with the trace id and
span id. To receive them, install a `LoggerProvider`. The state is not recorded unless you set
`record_state(True)`, and the API key is never recorded.

`events(…)` selects the signal for answers and retries: `"span"`, `"log"` or `"both"` (the
default). If you export traces and logs to one backend, set `"span"` or `"log"` to store each
event once. If your `opentelemetry-api` has no logs API, `"log"` and `"both"` raise
`ConfigError`, and the default sends the span event only.
[`docs/observability.md`](https://github.com/pedro-pscunha/guideme-python/blob/main/docs/observability.md)
has every field and the exporter settings.
[`examples/otlp`](https://github.com/pedro-pscunha/guideme-python/tree/main/examples/otlp) runs
it all against the live API, with a collector that prints what arrives.

## Configuration

`Guide.builder()` starts a `GuideBuilder`. Each setting returns the builder.

| Setting | Default | What it does |
|---|---|---|
| `api_key(ApiKey(…))` | none, required | The API key. |
| `base_url(…)` | `https://api.typesafe.ai` | The API origin. It cannot hold credentials. Give the final https origin. guideme follows redirects. |
| `model(Model(…))` | `jev-latest` | The model or alias to ask. |
| `policy(Policy(…))` | the defaults | The policy of the guide. The policy of a question wins over it. |
| `max_retries(n)` | `3` | Resends per call. `0` never resends. |
| `backoff(…)` | 500 ms | The base of the exponential backoff. |
| `timeout(…)` | 30 s | The limit for each phase of one attempt. |
| `transport(…)` / `async_transport(…)` | the transport of `httpx` | Send through your own `httpx` transport. |
| `record_state(True)` | off | Put the state JSON on the ask span. It is the data of your users. |
| `events(…)` | `"both"` | Send answers and retries as span events, log records, or both. |
| `from_env()` | | Apply the environment variables below. |

`transport(…)` and `timeout(…)` refuse each other, in either order, with a `ConfigError`: an
injected transport owns its deadlines. `transport(…)` is for `build()`, and `async_transport(…)`
is for `build_async()`. The wrong pair is a `ConfigError` too.

| Variable | Meaning |
|---|---|
| `TYPESAFE_API_KEY` | The API key. `Guide.from_env()` and `AsyncGuide.from_env()` require it. |
| `TYPESAFE_BASE_URL` | Optional. A different API origin. |
| `GUIDEME_MODEL` | Optional. The model or alias. The default is `jev-latest`. |

Build one guide per process and share it. A guide holds a connection pool, and it is safe to use
from many threads or tasks at once. A guide per request also works, but it opens a pool for each
request. `guide.with_policy(…)` returns a second guide over the same pool. The pool counts its
guides: after you close one, the other can still ask, and the pool closes with the last guide.

## Sync and async

`Guide` and `AsyncGuide` have the same methods over the same core. The differences are the
`await` and the `httpx` client below it.

```python
async with AsyncGuide.from_env() as guide:
    verdict: Verdict = await guide.ask(noul("Is this about billing?").detail(), ticket)
```

For the synchronous version, use `Guide`, `with`, and no `await`. `Guide.builder()` and
`AsyncGuide.builder()` return the same `GuideBuilder`: `.build()` gives a `Guide`, and
`.build_async()` gives an `AsyncGuide`. A guide closes at the end of its `with` block. Outside a
block, call `guide.close()`. If you close one guide twice, the second close does nothing. Do not
ask through a closed guide.

## Lower layers

The `guideme` package re-exports everything above, and `guideme.__all__` is that list. Some of
those names need a note:

- `Question` is what the five constructors return. To annotate a question that you store or
  pass on, use the concrete types `NoulQuestion`, `ChoiceQuestion`, `ScoreQuestion`,
  `DetailedNoul`, `DetailedChoice` and `DetailedScore`. A `dict` is invariant, so a
  `dict[str, NoulQuestion]` is not a `dict[str, Question[bool]]`.
- `Probability`, `Confidence`, `Key` and `Rank` are `NewType` brands. Only the wire creates
  them, after it validates the value, so `Probability(2.0)` in your code is not refused.
- `Model` names a model. `ApiKey` holds the key and never prints it.

Three modules are a second supported tier: `guideme.api`, `guideme.api.client` and
`guideme.policy`. Import them by their own path. The top level does not re-export them. They
have the same rule as the first tier: nothing in them is removed or renamed without a major
version and a `CHANGELOG.md` entry. Everything else in the package is private, whatever its
name looks like.

- `guideme.api` is the exact wire mirror of `POST /v1/systemone` and `GET /v1/models`.
  `guideme.api.__all__` lists the request and response models, its own `Usage`, and four
  adapters between them and the core.
- `guideme.api.client` holds `Client` and `AsyncClient`, to build requests yourself.
- `guideme.policy.resolve(answer, thresholds)` is the pure decision function. It takes a
  `Thresholds`: a `Policy` settled against the defaults. `spec/` holds its JSON Schemas and 42
  golden vectors, which use the same `Thresholds`.

`guideme.api` also declares a `Question`, `NoulQuestion`, `ChoiceQuestion`, `ScoreQuestion` and
`Usage` of its own: wire shapes, different classes from the ones above. You meet them only when
you build requests by hand, and
[`docs/design.md`](https://github.com/pedro-pscunha/guideme-python/blob/main/docs/design.md#sharp-edges)
explains these pairs.

## Other SDKs

Each guideme SDK is written from scratch in its own language, and they all turn one reading into
the same answer. They satisfy one contract: the wire schemas, the 42 golden policy vectors and
the interface shape. [guideme-rust](https://github.com/pedro-pscunha/guideme-rust) publishes it
under `spec/` and states it in
[`docs/contract.md`](https://github.com/pedro-pscunha/guideme-rust/blob/main/docs/contract.md).

| Language | Package | Repository |
|---|---|---|
| Rust | [`guideme`](https://crates.io/crates/guideme) | [guideme-rust](https://github.com/pedro-pscunha/guideme-rust) |
| Python | `guideme` | this repository |
| TypeScript | `@guideme/sdk` (not yet on npm) | [guideme-typescript](https://github.com/pedro-pscunha/guideme-typescript) |

The span, event and attribute names are shared too, so one dashboard reads every SDK.

## Development

To set up a clone, run `mise install`, `mise run sync` and `mise run hooks`. `mise run check` is
the gate.
[`CONTRIBUTING.md`](https://github.com/pedro-pscunha/guideme-python/blob/main/CONTRIBUTING.md)
and [`AGENTS.md`](https://github.com/pedro-pscunha/guideme-python/blob/main/AGENTS.md) have the
rules. Report a vulnerability privately, as
[`SECURITY.md`](https://github.com/pedro-pscunha/guideme-python/blob/main/SECURITY.md) tells,
and never in a public issue.

## License

MIT or Apache-2.0, at your option.
