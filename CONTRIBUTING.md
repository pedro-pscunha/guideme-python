# Contributing

guideme makes a TypeSafe Jev judgment usable as Python control flow. It is one distribution,
`guideme`. Its public surface is what `src/guideme/__init__.py` exports, plus a second
supported tier that callers import by their own path: the modules `guideme.api` and
`guideme.policy`, and the single name `guideme.question.Question`.
Everything else in the package is private. `AGENTS.md` and the README's **Lower layers**
section say what each tier promises.

Issues and pull requests are welcome. The one exception is a vulnerability, which goes through
[`SECURITY.md`](SECURITY.md) and never through a public issue.

## The rules live in AGENTS.md

[`AGENTS.md`](AGENTS.md) is the contributor guide and it is binding. It says which module owns
which seam, the invariants that hold everywhere in `src/guideme`, the policy semantics shared
with every other guideme SDK, what a new test is allowed to be, the commands, and the git
process. Read it fully before editing.

Three sections carry most of what a first change needs:

- `## Commands` — the gate and the other tasks.
- `## Git` — branching, hooks, commit messages.
- `## Changing the contract` — what to do when a change reaches the wire, the policy or a
  telemetry field name. Those are contract changes, they start in
  [`guideme-rust`](https://github.com/pedro-pscunha/guideme-rust), and they have a checklist.

## The loop

Tooling is managed by [mise](https://mise.jdx.dev), which pins `uv` and `gitleaks`; `uv` pins
everything else from `pyproject.toml` and `uv.lock`.

```sh
mise install      # fetch the tools
mise run sync     # install the locked environment
mise run hooks    # activate the tracked git hooks, once per clone
mise run test     # while you work
mise run check    # the full gate; the pre-push hook runs it too
```

The suite is small on purpose and `AGENTS.md` says what a new test may be. A pull request that
adds a mock of `policy`, of `Guide` or of the transport will be asked to replace it with a real
local server, a property test, or a typing proof.

## License

Contributions are dual-licensed under [MIT](LICENSE-MIT) or [Apache-2.0](LICENSE-APACHE), at
your option, the same terms as the package. Unless you say otherwise, anything you submit for
inclusion is licensed that way, with no additional terms.
