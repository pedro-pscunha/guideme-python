# Security policy

## Supported versions

`guideme` is at 0.1.0 and is not yet on PyPI. Once it is, the newest released version is the
only supported one. A published version on PyPI can never be replaced, so a fix ships as a new
version and the affected one is yanked.

## Reporting a vulnerability

Report it privately, not in a public issue. GitHub private vulnerability reporting is enabled
on this repository: [open an advisory](https://github.com/pedro-pscunha/guideme-python/security/advisories/new),
or go to the Security tab and choose **Report a vulnerability**. Only the maintainers can see
it.

This is a small project with nobody on call, so there is no response time to promise. You will
get an answer; it may not be the same day.

## Scope

In scope, because this package owns them:

- **The API key.** `ApiKey` prints as `ApiKey(***)` through both `repr` and `str`, is not JSON
  serialisable, and refuses to pickle; `tests/test_redaction.py` proves it. That the key is
  never recorded on a span and never returned in an error is an invariant held by a test and in
  review. Any path that puts the key somewhere a caller can read it is a vulnerability.
- **State confidentiality.** The state passed to `ask` is user data. Its content never reaches a
  span unless `record_state(True)` was set; its length, `guideme.state.bytes`, always does.
  Recording the content without that opt-in is a defect here.
- **The wire layer**: request construction, transport, status and error handling, the retry
  path, and any response the service could return that makes the decoder raise something other
  than a typed `GuidemeError`.
- Vulnerable dependencies reachable from library code. `mise run audit` runs `pip-audit` over
  the locked environment on every gate and weekly against an unchanged lock file.

Out of scope:

- **The TypeSafe service itself.** Its behaviour, its models, its authentication and its
  handling of the data you send are upstream, not here: see <https://docs.typesafe.ai>. This
  package is a client.
- What a model decides. A judgment you disagree with is not a vulnerability.
- `examples/`, which is illustrative and resolves outside the package's lock file.
- A key you leaked yourself, by printing it or committing a `.env`.
