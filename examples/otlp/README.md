# guideme OTLP example

Three support-triage questions against the live TypeSafe API, with every span exported over
OTLP to a collector that prints what it receives.

It is its own uv project, so its dependencies stay out of the library's resolution and
`uv sync --locked` here fails when `guideme` moves under it rather than quietly resolving
around it.

## Run it

From this directory:

```sh
docker run --rm -d --name guideme-otel -p 4317:4317 -p 4318:4318 \
  -v "$PWD:/conf:ro" \
  otel/opentelemetry-collector-contrib:latest --config=/conf/collector.yaml

export TYPESAFE_API_KEY=...
uv sync
uv run main.py
docker logs guideme-otel        # what arrived
docker stop guideme-otel        # --rm removes it
```

`collector.yaml` is the OpenTelemetry Collector with a debug exporter, which prints every span
it receives in full. Swap that exporter for your backend's when you are done looking.

The program runs without the collector too. It still prints its answers; the OTLP exporter
reports the connection failure when the provider is shut down.

## What it prints

Four answers, from three requests. A single yes/no question. Then a batch of three questions
in one request: the team to route to, the customer's frustration in full detail, and whether a
refund was asked for, with a fallback so an unsure answer does not fail the batch. Then the
same score question under a confidence floor of `0.999`, which nothing satisfies, so it raises
`UnsureError` and its span carries `error.type = "unsure"`.

## Where the spans go

The exporter reads the standard OpenTelemetry variables, so nothing here needs editing to
point it somewhere else.

| Variable | Effect |
|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | where to send; default `http://localhost:4317` for gRPC |
| `OTEL_EXPORTER_OTLP_HEADERS` | `key=value,key=value`, the usual place for a vendor's API key |
| `OTEL_SERVICE_NAME` | `service.name` on every span; this example defaults it to `support-triage` |
| `OTEL_RESOURCE_ATTRIBUTES` | `key=value,…`, for example `deployment.environment.name=prod` |

For a user interface on your laptop rather than collector logs, `grafana/otel-lgtm` is one
container with an OTLP receiver, Tempo and Grafana in front of it:

```sh
docker run --rm -d --name lgtm -p 3000:3000 -p 4317:4317 -p 4318:4318 grafana/otel-lgtm
```

[`docs/observability.md`](../../docs/observability.md) has the field tables for every span,
event and attribute this exports.
