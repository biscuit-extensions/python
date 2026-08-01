from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.python import setup
from src.python.extension import Python


class TestPythonExtension:
    def test_setup_and_registration(self, mock_api) -> None:
        setup(mock_api)
        assert "python" in mock_api.registered_extensions
        ext = mock_api.registered_extensions["python"]
        assert isinstance(ext, Python)

    def test_install_registers_commands_and_integrations(self, mock_api) -> None:
        ext = Python(mock_api)
        ext.install()

        # Commands registered
        commands = mock_api.commands.commands
        assert "python.run_file" in commands
        assert "python.select_interpreter" in commands
        assert "python.discover_tests" in commands
        assert "python.run_tests" in commands

        # Core registrations
        mock_api.register_comment_prefix.assert_called_with("python", "#")
        mock_api.register_run_command.assert_called()

        # Statusbar button
        assert ext.statusbar_button is not None
        assert ext.statusbar_button.visible is True
        assert "Python" in ext.statusbar_button.text

    def test_get_and_set_interpreter(self, mock_api, tmp_path) -> None:
        ext = Python(mock_api)
        ext.install()

        dummy_py = tmp_path / "custom_python.exe"
        dummy_py.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

        ext.set_interpreter(str(dummy_py))
        assert ext.get_interpreter() == str(dummy_py)
        assert ext.statusbar_button.text.startswith("Python")
        mock_api.notifications.info.assert_called()

    def test_get_interpreter_info(self, mock_api) -> None:
        ext = Python(mock_api)
        info = ext.get_interpreter_info(sys.executable)
        assert info["path"] == sys.executable
        assert info["version"] != ""
        assert isinstance(info["type"], str)

    def test_detect_virtualenvs(self, mock_api, tmp_path) -> None:
        ext = Python(mock_api)

        # Create mock .venv with python executable
        venv_dir = tmp_path / ".venv"
        scripts_dir = venv_dir / ("Scripts" if os.name == "nt" else "bin")
        scripts_dir.mkdir(parents=True)
        py_bin = scripts_dir / ("python.exe" if os.name == "nt" else "python")
        py_bin.write_text("dummy", encoding="utf-8")

        # Make executable
        os.chmod(py_bin, 0o777)

        venvs = ext.detect_virtualenvs(str(tmp_path))
        paths = [v["path"] for v in venvs]
        assert str(py_bin.resolve()) in paths

    def test_build_run_command(self, mock_api) -> None:
        ext = Python(mock_api)
        cmd = ext.build_run_command("path/to/script.py", ["--flag", "arg with space"])
        assert f'"{ext.get_interpreter()}" "path/to/script.py"' in cmd
        assert "--flag" in cmd
        assert '"arg with space"' in cmd

    def test_run_file_terminal_mode(self, mock_api) -> None:
        ext = Python(mock_api)
        res = ext.run_file("hello.py", terminal=True)
        assert res["status"] == "launched"
        assert res["mode"] == "terminal"
        assert len(mock_api.terminalmanager.opened_terminals) == 1

    def test_run_file_subprocess_mode(self, mock_api, tmp_path) -> None:
        ext = Python(mock_api)
        script = tmp_path / "test_exec.py"
        script.write_text("print('Biscuit Python Runner')\n", encoding="utf-8")

        res = ext.run_file(str(script), terminal=False)
        assert res["status"] == "completed"
        assert res["returncode"] == 0
        assert "Biscuit Python Runner" in res["stdout"]

    def test_parse_pytest_collect_output(self, mock_api) -> None:
        ext = Python(mock_api)
        raw_output = """
tests/test_api.py::test_login
tests/test_api.py::TestAuth::test_token_refresh
tests/test_models.py::test_user_create
"""
        items = ext.parse_pytest_collect_output(raw_output)
        assert len(items) == 3
        assert items[0]["id"] == "tests/test_api.py::test_login"
        assert items[0]["class"] == ""
        assert items[0]["name"] == "test_login"

        assert items[1]["id"] == "tests/test_api.py::TestAuth::test_token_refresh"
        assert items[1]["class"] == "TestAuth"
        assert items[1]["name"] == "test_token_refresh"

    def test_parse_pytest_output(self, mock_api) -> None:
        ext = Python(mock_api)
        sample_output = """
tests/test_first.py::test_pass PASSED                                    [ 33%]
tests/test_first.py::test_fail FAILED                                    [ 66%]
tests/test_second.py::test_skip SKIPPED                                  [100%]

=================================== FAILURES ===================================
__________________________________ test_fail ___________________________________
AssertionError: assert 1 == 2
======================== 1 failed, 1 passed, 1 skipped in 0.45s ========================
"""
        parsed = ext.parse_pytest_output(sample_output)
        assert parsed["passed"] == 1
        assert parsed["failed"] == 1
        assert parsed["skipped"] == 1
        assert parsed["total"] == 3
        assert parsed["duration"] == 0.45
        assert len(parsed["tests"]) == 3

    def test_discover_tests_ast_fallback(self, mock_api, tmp_path) -> None:
        ext = Python(mock_api)
        test_file = tmp_path / "test_sample.py"
        test_file.write_text(
            """
def test_standalone():
    assert True

class TestSuite:
    def test_method(self):
        assert 1 == 1
""",
            encoding="utf-8",
        )

        with patch("subprocess.run", side_effect=Exception("pytest not found")):
            tests = ext.discover_tests(str(tmp_path))

        assert len(tests) == 2
        names = {t["name"] for t in tests}
        assert "test_standalone" in names
        assert "test_method" in names

    def test_manifest_schema_and_dependencies(self) -> None:
        import tomllib

        manifest_path = Path(__file__).parent.parent / "extension.toml"
        assert manifest_path.exists()
        with open(manifest_path, "rb") as f:
            data = tomllib.load(f)

        assert data["extension"]["id"] == "python"
        assert data["extension"]["author"] == "tomlin7"
        assert data["extension"]["version"] == "0.2.0"
        assert "pytest>=8.0.0" in data["dependencies"]["python"]
        assert "python" in data["dependencies"]["tools"]
        assert len(data["contributes"]["commands"]) >= 4
