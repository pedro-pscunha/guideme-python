import ast
import re
from pathlib import Path

import guideme

from .conftest import REPO_ROOT

DOCUMENTED = frozenset(
    {
        "ApiKey",
        "AsyncGuide",
        "AuthError",
        "Choice",
        "Confidence",
        "ConfigError",
        "Guide",
        "GuideBuilder",
        "GuidemeError",
        "InvalidError",
        "Key",
        "Levels",
        "Model",
        "ModelInfo",
        "OverloadedError",
        "Policy",
        "Probability",
        "ProtocolError",
        "Rank",
        "Ranked",
        "RateLimitedError",
        "Scored",
        "Thresholds",
        "TransportError",
        "UnexpectedStatusError",
        "UnsureError",
        "Verdict",
        "choose",
        "choose_among",
        "fallback",
        "level",
        "noul",
        "option",
        "score",
        "score_levels",
    }
)

PACKAGE = REPO_ROOT / "src" / "guideme"
README = REPO_ROOT / "README.md"
WIRE_ONLY = frozenset({"httpx", "pydantic"})


def _module_name(path: Path) -> str:
    relative = path.relative_to(PACKAGE.parent).with_suffix("")
    return ".".join(part for part in relative.parts if part != "__init__")


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            found.add(node.module)
    return found


def test_public_surface_is_exactly_the_documented_list() -> None:
    assert set(guideme.__all__) == DOCUMENTED
    assert list(guideme.__all__) == sorted(guideme.__all__)
    assert all(hasattr(guideme, name) for name in guideme.__all__)
    # Inside code, not bare prose: `Thresholds` passed this check for a release while
    # appearing only as the English word starting a sentence, which documents nothing. A
    # name may sit inside a wider span, as `ModelInfo` does in `tuple[ModelInfo, ...]`,
    # so the test looks in every fenced block and every inline span rather than for the
    # name alone between backticks.
    prose = README.read_text(encoding="utf-8")
    code = "\n".join(re.findall(r"`{1,3}[^`]+`{1,3}", prose, re.DOTALL))
    assert not [name for name in guideme.__all__ if name not in code]


def test_httpx_and_pydantic_stay_behind_the_api_package() -> None:
    for path in sorted(PACKAGE.rglob("*.py")):
        module = _module_name(path)
        imported = _imports(path)
        if {name.split(".")[0] for name in imported} & WIRE_ONLY:
            assert module.startswith("guideme.api"), module
        if module == "guideme.policy":
            own = {name for name in imported if name.split(".")[0] == "guideme"}
            assert own <= {"guideme.errors", "guideme.scalars"}
