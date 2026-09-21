# pylint: disable=redefined-outer-name  # a pytest fixture is requested by its own name
import json
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from .conftest import as_list, as_object, as_str, narrow

NEGATIVES = Path(__file__).resolve().parent / "typing" / "expect_errors"
CONFIG = NEGATIVES / "pyrightconfig.json"
CASES = sorted(NEGATIVES.glob("*.py"))
MARKER = "# expect: "


@pytest.fixture(scope="session")
def reported() -> Mapping[str, set[str]]:
    """Every rule pyright reports as an error, by file, from one run over the directory.

    One run rather than one per case: pyright's start-up dominates, and the gate runs
    this on every commit. The directory has its own config because the project excludes
    it, and an excluded path stays excluded even when it is named on the command line.
    """
    finished = subprocess.run(  # noqa: S603 -- fixed argv, no shell, no caller input
        [sys.executable, "-m", "pyright", "--outputjson", "-p", str(CONFIG), str(NEGATIVES)],
        capture_output=True,
        text=True,
        check=False,
    )
    report = as_object(narrow(json.loads(finished.stdout)))
    analysed = as_object(report["summary"])["filesAnalyzed"]
    assert analysed == len(CASES), finished.stdout or finished.stderr

    rules: dict[str, set[str]] = {path.name: set() for path in CASES}
    for raw in as_list(report["generalDiagnostics"]):
        entry = as_object(raw)
        if as_str(entry["severity"]) != "error" or "rule" not in entry:
            continue
        rules[Path(as_str(entry["file"])).name].add(as_str(entry["rule"]))
    return rules


@pytest.mark.parametrize("path", CASES, ids=lambda path: path.stem)
def test_each_illegal_state_is_the_pyright_error_it_claims(
    path: Path, reported: Mapping[str, set[str]]
) -> None:
    first = path.read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith(MARKER), first
    expected = first.removeprefix(MARKER).strip()
    assert expected in reported[path.name], reported[path.name]
