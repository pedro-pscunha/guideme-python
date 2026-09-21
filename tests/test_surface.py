import ast
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
        "noul",
        "score",
        "score_levels",
    }
)

PACKAGE = REPO_ROOT / "src" / "guideme"
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


def test_httpx_and_pydantic_stay_behind_the_api_package() -> None:
    for path in sorted(PACKAGE.rglob("*.py")):
        module = _module_name(path)
        imported = _imports(path)
        if {name.split(".")[0] for name in imported} & WIRE_ONLY:
            assert module.startswith("guideme.api"), module
        if module == "guideme.policy":
            own = {name for name in imported if name.split(".")[0] == "guideme"}
            assert own <= {"guideme.errors", "guideme.scalars"}
