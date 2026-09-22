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
contract. The renderer appends the examples clause before the counterexamples clause — a
statement about the renderer, not about every string that reaches the wire, since a caller
who writes the labels into `what` by hand can put them in any order they like.

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

An absent clause appears as `[]`, not as `null` and not by omitting the key. A consumer must
read that as *no clause was declared* and rebuild the case without the argument, because an
empty clause written out is itself a refused declaration and so can never be a vector case.
`kind` is what picks the constructor to rebuild with: a `levels` case has to go through the
level constructor and a `choice` or `noul` case through the option one, which is how the vector
exercises the surface a caller writes rather than the renderer alone.

Items are inserted **verbatim**. Nothing is escaped, and the rendering is not required to be
reversible — no SDK parses a rendered rubric back into its parts, and none should be written
to. Rubric text is trusted: an SDK does not sanitise it, and it is the caller's to get right.

What the rules below do guarantee is narrower and worth stating exactly: **a string an SDK
renders from declared parts carries exactly the clauses those parts declared.** That is
rendering integrity, not input trust.

### Validation is over the declared items, not the rendered text

One sentence that decides every case a third implementer will hit, and the reason none of them
needs a special rule:

- `["a; b"]` renders identically to `["a", "b"]`, and is still **one** item. It passes the
  duplicate and shared-example checks that two items would meet.
- `" a"` and `"a"` are two items, because they are two strings.
- `"A"` and `"a"` are two items, for the same reason. There is no case folding.

An implementation that normalised, trimmed or split before comparing would refuse declarations
these SDKs accept. Compare the strings the caller declared, and nothing else.

`"; "` inside an item is therefore legal, and a newline is not — which looks inconsistent until
you see what each one does. `"; "` changes how many examples a reader sees, and
`"card declined; retry failed"` is ordinary prose a caller is entitled to write. A newline
changes **which clause** a reader thinks an item is in, which forges a clause the rubric never
declared. Only the second breaks the guarantee above. An item reading
`"Not this option: x"` with no newline in it is the same class as `"; "`: legal, because
without a line break it cannot become a clause.

### Which declarations are legal

These rules hold in every SDK, and each is refused where the rubric is written rather than at
ask time.

- An **empty** clause written out is refused. Leaving a clause off is how you say there is
  none; an empty sequence is a clause the caller wrote that says nothing.
- An **empty** entry within a clause is refused.
- A **duplicate** entry within one clause is refused.
- One string as an example of **two alternatives of the same question** is refused: it says one
  input belongs to both, which cannot be true.
- One string as **both an example and a counterexample of one alternative** is refused: it says
  the input does and does not belong there.
- One string as an example of one alternative and a **counterexample of another must be
  allowed**. This is the confusable-alternatives pattern the feature exists to serve, and an
  implementation that refused it would break the main use case.
- Examples are **attached to a blank rubric** is refused; a blank rubric with no examples is
  not. See below.
- A counterexample on a **level** is refused: an ordered scale has no "not this option".
- An item containing `U+000A` or `U+000D` is refused. `U+000A` is what the renderer joins
  clauses with, so an item carrying one would read as a clause that was never declared;
  `U+000D` is refused as hygiene, being pasted-text residue that breaks the `"; "`-joined line.
  The rule applies to **items only**. A newline in `what` stays legal: a bare `what` has to
  remain legal whatever it contains, so refusing it in the clause-bearing case would stop a
  caller writing ordinary multi-line prose without closing any path.

  The test is the literal codepoint — `"\n" in item` in Python, `item.contains('\n')` in Rust.
  Never `str.splitlines()`, never `str::lines()`, never an `is_control` predicate. Python's
  `splitlines()` splits on eight codepoints, adding `U+000B`, `U+000C`, `U+001C`, `U+001D`,
  `U+001E`, `U+0085`, `U+2028` and `U+2029`; Rust's `lines()` splits on `U+000A` alone. An
  implementer reaching for the idiomatic call in either language writes a rule the other does
  not have, and neither version looks wrong read on its own. `U+2028`, `U+2029` and `U+0085`
  are deliberately **not** refused: they are `White_Space`, so an item made only of them is
  already refused as empty, and embedded they cannot produce a clause boundary in the bytes an
  SDK emits. What a model's tokenizer makes of them is unmeasured and stays out of the
  contract, the same way the broader line-break set does.

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
`White_Space`'s 25, and the four extra are `U+001C`, `U+001D`, `U+001E` and `U+001F` — file,
group, record and unit separator. Nothing goes the other way: every `White_Space` codepoint is
one Python strips.

Those four are **C0** controls. The C0 block is `U+0000`–`U+001F`; C1 is `U+0080`–`U+009F` and
contains none of them. The distinction is worth stating because the point of writing this rule
down is that two SDKs must not describe the boundary differently, and naming the wrong block
would do exactly that.

So a rubric made only of those four is blank to Python and not to Rust. The difference is
documented rather than hidden, and it is harmless: C0 controls are never valid rubric text, so
no real declaration reaches it.

**A duplicate is an exact string match**, with no normalisation, no case folding and no
trimming. `"a"` and `" a"` are two different examples and may sit in the same clause. An
implementation that normalised before comparing would refuse declarations these SDKs accept,
which is the same divergence as a different label by another route.

**The two checks disagree about `" a"`, and they are meant to.** The emptiness check trims
before deciding, so `" a"` is not blank and `" "` is; the duplicate check does not trim, so
`" a"` and `"a"` are two entries. That reads like a bug until you see what each one is asking.
Emptiness asks whether the caller wrote anything at all, and a leading space does not change
the answer. Duplication asks whether two entries would put the same bytes in front of the
model, and a leading space does change that, because the text is rendered verbatim. Trimming
for one and not the other is the only pairing that keeps both questions honest.

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
