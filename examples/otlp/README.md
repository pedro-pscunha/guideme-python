# guideme OTLP example

A runnable program in its own uv project, so its dependencies stay out of the library's
resolution and `uv sync --locked` here fails when `guideme` moves under it.

```sh
uv sync
uv run main.py
```

Right now it builds the support-triage questions and prints the rubric each one sends. The
part that asks the real API and exports the resulting spans to a collector lands next, with
the `Guide` it needs.
