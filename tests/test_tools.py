import logging
from types import ModuleType
from typing import Any

import pkgutil
import pytest
from claude_agent_sdk import SdkMcpTool, tool


# ----- 4.1: example tools -----


def test_echo_module_exports_a_tool() -> None:
    from app.tools.echo import TOOLS

    assert len(TOOLS) == 1
    assert isinstance(TOOLS[0], SdkMcpTool)
    assert TOOLS[0].name == "echo"


def test_read_url_module_exports_a_tool() -> None:
    from app.tools.read_url import TOOLS

    assert len(TOOLS) == 1
    assert isinstance(TOOLS[0], SdkMcpTool)
    assert TOOLS[0].name == "read_url"


async def test_echo_handler_returns_input_unchanged() -> None:
    from app.tools.echo import TOOLS

    result = await TOOLS[0].handler({"text": "hello world"})
    assert result == {"content": [{"type": "text", "text": "hello world"}]}


async def test_echo_handler_validates_args_via_pydantic() -> None:
    from pydantic import ValidationError

    from app.tools.echo import TOOLS

    with pytest.raises(ValidationError):
        await TOOLS[0].handler({"text": 123})  # type: ignore[arg-type]


async def test_echo_handler_rejects_missing_required_field() -> None:
    from pydantic import ValidationError

    from app.tools.echo import TOOLS

    with pytest.raises(ValidationError):
        await TOOLS[0].handler({})


# ----- 4.2: production smoke -----


def test_app_tools_registers_both_examples() -> None:
    from app.tools import ALLOWED_TOOLS, MCP_SERVER

    assert MCP_SERVER is not None
    assert sorted(ALLOWED_TOOLS) == ["mcp__local__echo", "mcp__local__read_url"]


# ----- 4.2: extract_tools (TOOLS-attribute contract) -----


def _make_module(name: str = "fake") -> ModuleType:
    return ModuleType(name)


def _make_tool(name: str) -> SdkMcpTool[Any]:
    @tool(name, "fixture", {})
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": ""}]}

    return handler  # type: ignore[return-value]


def test_extract_tools_returns_empty_when_TOOLS_absent() -> None:
    from app.tools import extract_tools

    assert extract_tools("fake", _make_module()) == []


def test_extract_tools_collects_valid_entries() -> None:
    from app.tools import extract_tools

    module = _make_module()
    module.TOOLS = [_make_tool("a"), _make_tool("b")]  # type: ignore[attr-defined]

    result = extract_tools("fake", module)
    assert [t.name for t in result] == ["a", "b"]


def test_extract_tools_logs_when_TOOLS_is_not_a_list(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.tools import extract_tools

    module = _make_module()
    module.TOOLS = "not a list"  # type: ignore[attr-defined]

    with caplog.at_level(logging.ERROR, logger="app.tools"):
        assert extract_tools("fake", module) == []
    assert any("malformed" in r.getMessage().lower() for r in caplog.records)


def test_extract_tools_filters_non_sdkmcptool_entries(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.tools import extract_tools

    module = _make_module()
    module.TOOLS = [_make_tool("real"), "imposter", 42]  # type: ignore[attr-defined]

    with caplog.at_level(logging.ERROR, logger="app.tools"):
        result = extract_tools("fake", module)
    assert [t.name for t in result] == ["real"]
    assert sum("non-SdkMcpTool" in r.getMessage() for r in caplog.records) == 2


# ----- 4.2: walk_package (broken-module tolerance) -----


def test_walk_package_skips_modules_that_fail_to_import(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from app.tools import walk_package

    fake_pkg = _make_module("fake_pkg")
    fake_pkg.__path__ = []  # type: ignore[attr-defined]

    fake_modules = [
        pkgutil.ModuleInfo(module_finder=None, name="fake_pkg.bad", ispkg=False),
        pkgutil.ModuleInfo(module_finder=None, name="fake_pkg.good", ispkg=False),
    ]
    monkeypatch.setattr(
        "app.tools.pkgutil.iter_modules", lambda paths, prefix="": iter(fake_modules)
    )

    good_module = _make_module("fake_pkg.good")
    good_module.TOOLS = [_make_tool("good_tool")]  # type: ignore[attr-defined]

    def fake_import(name: str) -> ModuleType:
        if name == "fake_pkg.bad":
            raise ImportError("intentionally broken")
        if name == "fake_pkg.good":
            return good_module
        raise AssertionError(f"unexpected import: {name}")

    monkeypatch.setattr("app.tools.importlib.import_module", fake_import)

    with caplog.at_level(logging.ERROR, logger="app.tools"):
        result = walk_package(fake_pkg)

    assert [t.name for t in result] == ["good_tool"]
    assert any("fake_pkg.bad" in r.getMessage() for r in caplog.records)
