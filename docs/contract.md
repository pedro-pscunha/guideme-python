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
into is what goes on the wire. Every guideme SDK composes the same string from the same parts,
and `spec/vectors/rubric.json` is where the renderers are held to each other. All three kinds
of question carry them, a noul's yes and no included.

The algorithm, in full, because this is what a new SDK implements from:

```
render(what, examples, counterexamples) -> string:
    if examples is empty and counterexamples is empty:
        return what                     # unchanged, byte for byte
    lines = [what]
    if examples:
        lines.append("Examples: " + join(examples, "; "))
    if counterexamples:
        lines.append("Not this option: " + join(counterexamples, "; "))
    return join(lines, "\n")
```

The two labels are literal and exact: `"Examples: "` and `"Not this option: "`, each with its
trailing space. They are not formatting to be chosen locally — `Not this option` was measured
against `Not` and `Counterexamples` on the same confusable case and reached the correct option
with the highest mean probability of the three, so a different label is a different, and worse,
contract. The examples clause always precedes the counterexamples clause.

`what` is used verbatim: never trimmed, never re-punctuated. Newline separation is what makes
that safe, since examples often end in `?` and a space-joined format would need a trailing `.`
that produces `Where is my refund?.`.

**Order within a clause is part of it too.** Examples and counterexamples render in the order
they were written, never sorted and never collapsed into a set, because two SDKs ordering
differently would send different bytes for the same declaration.

`spec/vectors/rubric.json` carries one object per case with five fields: `kind`, one of
`choice`, `levels` or `noul`, which says what surface the case came from; `what`, the rubric
text; `examples` and `counterexamples`, the two clauses in declaration order; and `rendered`,
the exact bytes the algorithm above must produce. The `noul` cases come from the runtime
renderer rather than a derive, so they are the vector's only cover for that path.

### What counts as blank, and what counts as a duplicate

Two rules a third implementer would otherwise have to guess, and would guess differently.

**A blank rubric is only an error when examples are attached to it.** A rubric that carries no
examples is never refused for its text, whatever that text is: it means what it meant before
this feature existed, and a patch release does not get to redefine it. Attaching examples to a
blank rubric is the error, because they describe something that is not there. Both SDKs draw
the line in the same place — Python inside `option()`, `level()` and `fallback()`, Rust in the
derive and in `Rubric::into_wire()` — so a declaration is legal in both or in neither.

**"Blank" means Unicode `White_Space`.** Rust's `str::trim` is exactly that property. Python's
`str.strip()` is a superset: measured against the current runtime it strips 29 codepoints to
`White_Space`'s 25, the four extra being `U+001C`–`U+001F`, the file, group, record and unit
separators. So a rubric made only of those characters is blank to Python and not to Rust. That
boundary is stated rather than hidden, and it is deliberate: those four are C0 controls that are
never valid rubric text, so no real declaration reaches the difference.

**A duplicate is an exact string match**, with no normalisation, no case folding and no
trimming. `"a"` and `" a"` are two different examples and may sit in the same clause. An
implementation that normalised before comparing would refuse declarations these SDKs accept,
which is the same divergence as a different label by another route.

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
