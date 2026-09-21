#!/usr/bin/env bash
# Shared resolvers for the tracked hooks in this directory. Sourced, not executed.
#
# Each one prints the path to an executable. The mise shim comes first so the
# version pinned in mise.toml is the one that runs, then PATH, then the fixed
# install locations a stripped PATH (a git GUI, a non-direnv shell) will not carry.
#
# Both fail loud. A hook that cannot find its tool refuses the operation rather
# than skipping the check — a check that silently does not run is the failure
# mode these hooks exist to prevent.

_first_executable() {
	local candidate
	for candidate in "$@"; do
		if [ -n "$candidate" ] && [ -x "$candidate" ]; then
			printf '%s\n' "$candidate"
			return 0
		fi
	done
	return 1
}

resolve_gitleaks() {
	if _first_executable \
		"$HOME/.local/share/mise/shims/gitleaks" \
		"$(command -v gitleaks 2>/dev/null || true)" \
		/opt/homebrew/bin/gitleaks \
		/usr/local/bin/gitleaks; then
		return 0
	fi
	echo "FATAL: gitleaks not found — refusing to run without a secret scan." >&2
	echo "       It is pinned in mise.toml; 'mise install' fetches it." >&2
	return 1
}

resolve_mise() {
	if _first_executable \
		"$(command -v mise 2>/dev/null || true)" \
		/opt/homebrew/bin/mise \
		/usr/local/bin/mise \
		"$HOME/.local/bin/mise"; then
		return 0
	fi
	echo "FATAL: mise not found — the gate lives in mise.toml and cannot run." >&2
	echo "       Install it: https://mise.jdx.dev" >&2
	return 1
}

# Run one gitleaks scan. Exit 0 is clean, 2 is a leak, anything else means the
# scanner itself did not run (bad config, unreadable repository). The caller
# refuses on both non-zero cases, but tells the user which one it was: a scan
# that did not run is not a clean scan, and it is not a secret either.
scan_for_secrets() {
	local gitleaks
	gitleaks="$(resolve_gitleaks)" || return 1
	"$gitleaks" "$@" --no-banner --redact --exit-code 2
}

# Turn a scan_for_secrets status into a message and a refusal. $1 is the status,
# $2 is a bare noun phrase for what was scanned ("staged change", "push to
# refs/heads/main"), $3 is the command to inspect it with, or empty.
#
# `unlistable` is not a gitleaks status: it is what a caller passes when git could
# not enumerate the commits to scan, so gitleaks was never given anything to do.
# Blaming the scanner for that would send the reader to the wrong tool.
refuse_on_secrets() {
	local status="$1" scanned="$2" inspect="$3"
	case "$status" in
	0) return 0 ;;
	2)
		echo "gitleaks: potential secret in the $scanned — blocked." >&2
		[ -n "$inspect" ] && echo "  Inspect with: $inspect" >&2
		;;
	unlistable)
		echo "git could not list the commits for the $scanned, so there was nothing to hand gitleaks — blocked; unlisted is not clean." >&2
		[ -n "$inspect" ] && echo "  Reproduce with: $inspect" >&2
		;;
	*)
		echo "gitleaks did not run (exit $status), so the $scanned is unscanned — blocked; unscanned is not clean." >&2
		;;
	esac
	return 1
}
