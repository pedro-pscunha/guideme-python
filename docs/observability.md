# Observability

guideme emits OpenTelemetry spans and events and nothing else. It installs no tracer
provider, no exporter and no logging handler. Until the application installs a provider,
every call goes to OpenTelemetry's no-op implementation and costs almost nothing.

What it emits is shaped by the OpenTelemetry semantic conventions: the GenAI conventions
for the ask span, the HTTP client conventions for each request, and the error conventions
for failures. Any OTLP backend, and any product that understands `gen_ai.*` attributes,
reads it without a mapping step.

**These names are the contract.** Every guideme SDK emits the same ones so that one
dashboard reads all of them. Renaming, adding or removing a field here is a contract
change: it goes through `docs/contract.md` in
[`guideme-rust`](https://github.com/pedro-pscunha/guideme-rust) and `CHANGELOG.md`, and it
is announced to every other SDK.

## Shape

```
guideme.ask                      span, kind CLIENT, one per ask
├── POST /v1/systemone           span, kind CLIENT, one per HTTP attempt
│   └── guideme.retry            event, only when that attempt was throttled
└── guideme.answer               event, one per question
```

The ask span and the answer events come from the instrumentation scope `guideme`; the HTTP
spans and the retry events from `guideme.api`. Filter on those the way the Rust SDK's
targets are filtered.

A batch of three questions is one ask span, one HTTP span if the first attempt succeeds,
and three answer events. A retried request is one ask span with sibling HTTP spans, each
with its own status code. `models()` has no ask span of its own: it emits a bare
`GET /v1/models` span under whatever span the caller is in.

### Span `guideme.ask`

| Field | Type | Meaning |
|---|---|---|
| `gen_ai.provider.name` | str | `typesafe` |
| `gen_ai.operation.name` | str | `ask` |
| `gen_ai.request.model` | str | alias or id sent, `jev-latest` by default |
| `gen_ai.response.model` | str | versioned id that answered, for example `jev-1.13.0` |
| `gen_ai.usage.input_tokens` | int | billed tokens |
| `gen_ai.usage.output_tokens` | int | free tokens |
| `server.address`, `server.port` | str, int | where the request went |
| `guideme.questions` | int | questions in the request |
| `guideme.state.bytes` | int | UTF-8 length of the serialised state |
| `guideme.state` | str | the state JSON, only when `record_state(True)` is set |
| `error.type` | str | the error's `kind`, only when the ask failed |

The span kind is `CLIENT`. `gen_ai.response.model` and `gen_ai.usage.*` are recorded when
the response arrives, so they are absent on a span that failed in transport.
`guideme.state` is user data and is never recorded unless asked for. The API key appears
in no field; `tests/test_redaction.py` walks every exported attribute to prove it.

Two deliberate deviations from the GenAI conventions. The span keeps the fixed name
`guideme.ask` rather than the `{operation} {model}` pattern, because a fixed name is what
dashboards key on and the model is on the span as an attribute. And `gen_ai.operation.name`
is the custom value `ask` rather than a well-known one such as `chat`: a Jev judgment sends
structured questions and gets probabilities back, and a product keying on `chat` would read
it as a conversation.

### Spans `POST /v1/systemone` and `GET /v1/models`

| Field | Type | Meaning |
|---|---|---|
| `http.request.method` | str | `POST` or `GET` |
| `server.address`, `server.port` | str, int | host and port of the base URL |
| `url.full` | str | the request URL; a base URL carrying credentials is refused at build time, so this never holds a secret |
| `url.template` | str | `/v1/systemone` or `/v1/models`, the low-cardinality form of the path |
| `http.request.resend_count` | int | ordinal of the retry, absent on the first attempt |
| `http.response.status_code` | int | absent when no response arrived |
| `error.type` | str | the status code as text when a response arrived, else the error's `kind` |

The span kind is `CLIENT`. A `429` that was retried and then succeeded is one failed
attempt span next to one successful one, exactly as the HTTP conventions describe a resend.

The HTTP conventions leave the status unset for a 3xx and let an instrumentation with more
context set it more precisely. guideme has that context: its contract makes `200` the only
success, redirects are followed by the client, and anything else that reaches this code is
raised as an error. So every non-200 marks the attempt.

### Event `guideme.answer`

| Field | Type | Meaning |
|---|---|---|
| `guideme.question` | str | `q0..qN`, encounter order |
| `guideme.kind` | str | `noul`, `choice` or `score` |
| `guideme.outcome` | str or int | `yes`/`no`/`unsure`, the chosen key, or the level index |
| `guideme.probability` | float | noul only, the probability of yes |
| `guideme.confidence` | float | choice and score, the reported confidence |
| `guideme.value` | float | score only, the expected value |
| `guideme.unsure` | bool | the policy's verdict |
| `guideme.yes_above`, `guideme.no_below`, `guideme.min_confidence` | float | the settled thresholds behind the verdict |

The event is emitted from the resolved outcome, before the unsure ladder picks a fallback,
so it says what the model answered rather than what the caller ended up with.

### Event `guideme.retry`

Emitted inside the throttled attempt's span, just before the wait. It is the
warning-equivalent: OpenTelemetry span events carry no severity, so where the Rust SDK logs
this at `WARN`, here the event's presence is the signal.

| Field | Type | Meaning |
|---|---|---|
| `http.response.status_code` | int | `429` or `529` |
| `guideme.retry.attempt` | int | ordinal of the resend about to be made; `1` for the first retry |
| `guideme.retry.delay_ms` | int | how long guideme is about to wait |

### Errors

guideme records no exception event and emits no log record. A failure is raised as a typed
error and marked on the span: `error.type` gets the stable name from the error's `kind`, and
the span status becomes `ERROR` with the message as its description. That is what dashboards
filter on, and it keeps the caller in charge of whether and where the failure is logged.
Both spans are started with `record_exception=False` and `set_status_on_exception=False` so
that nothing is recorded behind guideme's back.

`error.type` values on the ask span: `auth`, `invalid`, `rate_limited`, `overloaded`,
`transport`, `unexpected_status`, `protocol`, `unsure`, `config`. On an HTTP span it is the
status code as text when a response arrived, otherwise one of those names.

The status description is the error's message, except for `invalid` and
`unexpected_status`: those errors carry the verbatim response body, which could echo the
state, so the span only says which status it was and the body stays on the raised error. A
malformed body is reported as the field that failed and why, never as the value that failed,
for the same reason.

Numbers are recorded as `int`, never as a string. OpenTelemetry attributes are signed
integers, and a count exported as text stops being aggregatable.

### Mapping from the Rust SDK

The Rust SDK writes `otel.kind`, `otel.status_code` and `otel.status_description` as span
fields, because `tracing-opentelemetry` reads those names to set the exported span's kind and
status. OpenTelemetry for Python sets both directly, so those three names do not exist here:

| Rust field | Here |
|---|---|
| `otel.kind = "client"` | `SpanKind.CLIENT` |
| `otel.status_code = "ERROR"` | `span.set_status(StatusCode.ERROR, …)` |
| `otel.status_description` | the description argument of that same call |

The exported spans are identical; only the way the SDK is told differs. Levels differ the
same way: Rust's spans and answer events are `INFO` and its retry event is `WARN`, while
OpenTelemetry span events carry no severity at all.

## Installing a provider

guideme depends on `opentelemetry-api` only. The application installs the SDK.

```sh
pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc
```

```python
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

provider = TracerProvider(resource=Resource.create({"service.name": "support-triage"}))
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(provider)

# ... the application runs ...

provider.shutdown()  # the batch processor drops what it is holding without this
```

`set_tracer_provider` takes effect once per process and guideme resolves its tracers
lazily, so it works whether it runs before or after `import guideme`. Call `shutdown`
before the process exits, or the last batch never leaves.

To see the same data on the console instead, swap the exporter for
`opentelemetry.sdk.trace.export.ConsoleSpanExporter`. `examples/otlp` is a runnable version
of all of this against the live API, with a collector config that prints what it receives.

### Configuration by environment

The Python SDK reads the standard variables, so the code above needs no change to point at
a different backend.

| Variable | Effect |
|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | where to send; default `http://localhost:4317` for gRPC, `4318` for HTTP |
| `OTEL_EXPORTER_OTLP_HEADERS` | `key=value,key=value`, the usual place for a vendor's API key |
| `OTEL_EXPORTER_OTLP_TIMEOUT` | seconds per export batch, default `10` |
| `OTEL_EXPORTER_OTLP_COMPRESSION` | `gzip` |
| `OTEL_EXPORTER_OTLP_TRACES_*` | the same, for traces only; these win over the generic ones |
| `OTEL_SERVICE_NAME` | `service.name` on every span |
| `OTEL_RESOURCE_ATTRIBUTES` | `key=value,...`, for example `deployment.environment.name=prod` |
| `OTEL_TRACES_SAMPLER`, `OTEL_TRACES_SAMPLER_ARG` | `always_on`, `always_off`, `traceidratio`, `parentbased_traceidratio` with the ratio in `_ARG` |
| `OTEL_BSP_*` | batch size and delay of the span processor |
| `OTEL_SDK_DISABLED` | `true` turns the SDK off without touching the code |

### Where to point it

- **Look at the raw data.** The OpenTelemetry Collector with a debug exporter prints every
  span it receives.

  ```sh
  docker run --rm -d --name guideme-otel -p 4317:4317 -p 4318:4318 \
    -v "$PWD/examples/otlp:/conf:ro" \
    otel/opentelemetry-collector-contrib:latest --config=/conf/collector.yaml
  docker logs -f guideme-otel
  ```

- **A UI on your laptop.** `grafana/otel-lgtm` is one container with an OTLP receiver,
  Tempo for traces, Loki for logs and Grafana in front.

  ```sh
  docker run --rm -d --name lgtm -p 3000:3000 -p 4317:4317 -p 4318:4318 grafana/otel-lgtm
  ```

- **A vendor.** Set `OTEL_EXPORTER_OTLP_ENDPOINT` to their OTLP endpoint and put the API key
  in `OTEL_EXPORTER_OTLP_HEADERS`; their documentation names the header. Products with an
  LLM observability view pick the ask span up as a model call through its `gen_ai.*`
  attributes.

- **Metrics without instrumenting.** The collector's `spanmetrics` connector turns these
  spans into request, error and duration series, grouped by any attribute. Token cost per
  model is `sum(gen_ai.usage.input_tokens) by (gen_ai.response.model)` over the ask spans.

## Useful queries

- Cost per call: sum `gen_ai.usage.input_tokens` by `gen_ai.response.model`.
- Provider trouble: HTTP spans with `http.request.resend_count` set, or `guideme.retry`
  events per minute. Ask spans with `error.type` in `rate_limited`, `overloaded`,
  `transport` are the ones that gave up.
- Threshold tuning: on `guideme.answer`, the rate of `guideme.unsure=true` per
  `guideme.question`, next to the `guideme.min_confidence` recorded beside it. A band that
  is too wide shows up as reviewers drowning; too narrow shows up as wrong routes.
- Drift: `error.type=protocol` means the API's shape changed.
- One customer's ticket: the trace id links the ask span, its HTTP attempts and every
  answer, so a single trace view explains one decision end to end.
