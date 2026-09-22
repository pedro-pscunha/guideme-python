# The guideme contract

Every guideme SDK is written from scratch in its own language. What they share is a contract,
and this repository is not where it is published. It lives in
[guideme-rust](https://github.com/pedro-pscunha/guideme-rust): the statement is
[`docs/contract.md`](https://github.com/pedro-pscunha/guideme-rust/blob/main/docs/contract.md)
and the machine-readable half is that repository's `spec/`. Read it there. Its three parts are
wire fidelity, policy conformance and interface shape, and this package satisfies all three.

`spec/` here is a copy of `spec/` there, and `spec/SOURCE` holds the guideme-rust commit it was
taken from. `spec/schema/*.json` are the shapes of `POST /v1/systemone`, which the wire models
validate against on the docs examples. `spec/vectors/policy.json` is the 42 golden vectors,
every one of which is a parametrised case of `tests/test_policy_vectors.py`: an entry with an
`outcome` must equal it by JSON equality, an entry with `error: "protocol"` must raise
`ProtocolError`. Nothing in `spec/` is edited here. A behaviour difference between this package
and another SDK is either a bug here or an ambiguity to resolve upstream; it is never fixed by
changing the copy.

The rendered rubric string is a contract item too. An alternative written with `option(…)`,
`level(…)` or `fallback(…)` carries its examples beside its text, and the string those compose
into — clauses joined with a newline, items within one joined with `"; "`, the text verbatim,
and the text alone when there are no examples — is what goes on the wire. Every guideme SDK
composes the same string from the same parts, and `spec/vectors/rubric.json` is where the
renderers are held to each other. All three kinds of question carry them, a noul's yes and no
included.

**Order is part of it.** Examples and counterexamples render in the order they were written,
never sorted and never collapsed into a set, because two SDKs ordering differently would send
different bytes for the same declaration.

One asymmetry is deliberate. `choose_among` and `score_levels` here take an `option(…)` or a
`level(…)` value; Rust's equivalents keep taking a plain string, because widening their
signatures risks inference breakage for existing callers on a path that can already pass a
string its own renderer composed. It is revisited at 0.2.0. Equivalent inputs put identical
bytes on the wire either way, which is what the contract actually promises.

Drift is caught rather than trusted. `mise run spec-check` clones guideme-rust, diffs its `spec/`
against this one and fails on any difference except `spec/SOURCE`, which is provenance and has
no counterpart upstream. It runs on every pull request as the `spec-drift` job of
`.github/workflows/ci.yml`, which is a required check, and weekly from
`.github/workflows/spec-drift.yml`, so a contract change landing upstream turns this repository
red without anybody watching for it. `AGENTS.md` has the procedure for taking a new `spec/`.
