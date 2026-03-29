"""Tests for new tools: edit_file, planning, shadow backup, run_python, permissions."""

from pathlib import Path

import pytest

from nixx.tools.file_tools import EditFileTool, ReadFileTool, WriteFileTool
from nixx.tools.permissions import is_path_allowed
from nixx.tools.planning import ReadPlanTool, WritePlanTool, get_current_plan
from nixx.tools.run_python import RunPythonTool
from nixx.tools.shadow import shadow_backup


# ── Shadow backup ─────────────────────────────────────────────────────────────


def test_shadow_backup_creates_copy(tmp_path: Path) -> None:
    f = tmp_path / "test.txt"
    f.write_text("original content")
    result = shadow_backup(f)
    assert result is not None
    assert result.exists()
    assert result.read_text() == "original content"


def test_shadow_backup_missing_file(tmp_path: Path) -> None:
    f = tmp_path / "nonexistent.txt"
    assert shadow_backup(f) is None


# ── Permissions ───────────────────────────────────────────────────────────────


def test_is_path_allowed_scratch(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    target = scratch / "file.txt"
    assert is_path_allowed(target, scratch, []) is True


def test_is_path_allowed_granted(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    granted = tmp_path / "other"
    granted.mkdir()
    target = granted / "file.txt"
    assert is_path_allowed(target, scratch, [str(granted)]) is True


def test_is_path_allowed_denied(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    target = tmp_path / "forbidden" / "file.txt"
    assert is_path_allowed(target, scratch, []) is False


# ── Edit file tool ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_edit_file_replaces(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    f = scratch / "test.txt"
    f.write_text("hello world foo bar")

    tool = EditFileTool(scratch)
    result = await tool.execute(path="test.txt", old_string="world", new_string="earth")
    assert result.success
    assert f.read_text() == "hello earth foo bar"


@pytest.mark.asyncio
async def test_edit_file_rejects_multiple(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    f = scratch / "test.txt"
    f.write_text("aaa bbb aaa")

    tool = EditFileTool(scratch)
    result = await tool.execute(path="test.txt", old_string="aaa", new_string="ccc")
    assert not result.success
    assert "2 times" in result.error


@pytest.mark.asyncio
async def test_edit_file_not_found(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    tool = EditFileTool(scratch)
    result = await tool.execute(path="missing.txt", old_string="x", new_string="y")
    assert not result.success


# ── Planning tools ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_plan_read_empty(tmp_path: Path) -> None:
    tool = ReadPlanTool(tmp_path)
    result = await tool.execute()
    assert result.success
    assert "No plan" in result.result


@pytest.mark.asyncio
async def test_plan_write_and_read(tmp_path: Path) -> None:
    write = WritePlanTool(tmp_path)
    read = ReadPlanTool(tmp_path)

    result = await write.execute(content="- [ ] Step 1\n- [ ] Step 2")
    assert result.success

    result = await read.execute()
    assert result.success
    assert "Step 1" in result.result


def test_get_current_plan_none(tmp_path: Path) -> None:
    assert get_current_plan(tmp_path) is None


def test_get_current_plan_exists(tmp_path: Path) -> None:
    (tmp_path / ".plan.md").write_text("my plan")
    assert get_current_plan(tmp_path) == "my plan"


# ── Run python tool ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_python_success(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    tool = RunPythonTool(scratch)
    result = await tool.execute(code="print(2 + 2)")
    assert result.success
    assert "4" in result.result


@pytest.mark.asyncio
async def test_run_python_error(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    tool = RunPythonTool(scratch)
    result = await tool.execute(code="raise ValueError('boom')")
    assert not result.success
    assert "boom" in result.error


@pytest.mark.asyncio
async def test_run_python_timeout(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    tool = RunPythonTool(scratch)
    result = await tool.execute(code="import time; time.sleep(10)", timeout=1)
    assert not result.success
    assert "timed out" in result.error


# ── File tools with allowed directories ───────────────────────────────────────


@pytest.mark.asyncio
async def test_read_file_absolute_granted(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    granted = tmp_path / "granted"
    granted.mkdir()
    (granted / "data.txt").write_text("hello from granted dir")

    tool = ReadFileTool(scratch)
    tool._allowed_dirs = [str(granted)]
    result = await tool.execute(path=str(granted / "data.txt"))
    assert result.success
    assert "hello from granted dir" in result.result


@pytest.mark.asyncio
async def test_read_file_absolute_denied(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    denied = tmp_path / "denied"
    denied.mkdir()
    (denied / "secret.txt").write_text("nope")

    tool = ReadFileTool(scratch)
    result = await tool.execute(path=str(denied / "secret.txt"))
    assert not result.success
    assert "outside allowed" in result.error


@pytest.mark.asyncio
async def test_write_file_creates_shadow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    f = scratch / "test.txt"
    f.write_text("original")

    # Redirect shadow root to tmp
    monkeypatch.setattr("nixx.tools.shadow._SHADOW_ROOT", tmp_path / ".shadows")

    tool = WriteFileTool(scratch)
    result = await tool.execute(path="test.txt", content="updated")
    assert result.success
    assert f.read_text() == "updated"
    # Shadow should exist
    shadows = list((tmp_path / ".shadows").rglob("test.txt.*"))
    assert len(shadows) == 1
    assert shadows[0].read_text() == "original"
