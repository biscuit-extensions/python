from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
import typing
from pathlib import Path

from biscuit.extensions import Extension

if typing.TYPE_CHECKING:
    from biscuit.api import ExtensionsAPI


class Python(Extension):
    """Production Python language support, environment detector, runner, and pytest integration."""

    def __init__(self, api: ExtensionsAPI) -> None:
        super().__init__(api)
        self.api = api
        self.active_interpreter = self.get_interpreter()
        self.statusbar_button = None
        self.api.logger.info("Python extension loaded.")

    def get_interpreter(self) -> str:
        """Return current Python interpreter executable path."""
        if hasattr(self, "active_interpreter") and self.active_interpreter:
            return self.active_interpreter
        return sys.executable

    def set_interpreter(self, interpreter_path: str) -> None:
        """Set active Python interpreter executable and refresh UI status."""
        if not os.path.isfile(interpreter_path) and not shutil.which(interpreter_path):
            self.api.logger.warning(f"Interpreter path not found: {interpreter_path}")
            return

        self.active_interpreter = interpreter_path
        info = self.get_interpreter_info(interpreter_path)
        label = f"Python {info.get('version', '')} ({info.get('name', 'custom')})".strip()

        if self.statusbar_button:
            self.statusbar_button.change_text(label)

        self.api.register_run_command("python", f'"{interpreter_path}" {{filename}}')
        self.api.notifications.info(f"Selected Python Interpreter: {label}")

    def get_interpreter_info(self, interpreter_path: str | None = None) -> dict[str, str]:
        """Inspect interpreter details: version string, name, and environment type."""
        path = interpreter_path or self.get_interpreter()
        info = {
            "path": path,
            "version": "",
            "type": "system",
            "name": "system",
        }

        # Check virtualenv signatures in path
        lower_path = path.lower()
        if ".venv" in lower_path or "venv" in lower_path or "env" in lower_path:
            info["type"] = "venv"
            info["name"] = Path(path).parent.parent.name
        elif "conda" in lower_path or "miniconda" in lower_path or "anaconda" in lower_path:
            info["type"] = "conda"
            info["name"] = "conda"

        # Query version
        if path == sys.executable:
            v = sys.version_info
            info["version"] = f"{v.major}.{v.minor}.{v.micro}"
        else:
            try:
                res = subprocess.run(
                    [path, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                output = (res.stdout or res.stderr).strip()
                match = re.search(r"Python\s+([\d\.]+)", output)
                if match:
                    info["version"] = match.group(1)
            except Exception:
                v = sys.version_info
                info["version"] = f"{v.major}.{v.minor}.{v.micro}"

        return info

    def detect_virtualenvs(self, base_dir: str | None = None) -> list[dict[str, str]]:
        """Scan workspace and system for virtual environments and Python binaries."""
        target = Path(base_dir or os.getcwd())
        results: list[dict[str, str]] = []
        seen_paths: set[str] = set()

        def add_candidate(binary: Path, env_type: str, env_name: str) -> None:
            resolved = str(binary.resolve()) if binary.exists() else str(binary)
            if resolved not in seen_paths and binary.exists() and os.access(binary, os.X_OK):
                seen_paths.add(resolved)
                results.append({
                    "path": resolved,
                    "name": env_name,
                    "type": env_type,
                    "version": self.get_interpreter_info(resolved).get("version", ""),
                })

        # 1. Active VIRTUAL_ENV environment variable
        active_env = os.environ.get("VIRTUAL_ENV")
        if active_env:
            env_path = Path(active_env)
            for bin_name in ["Scripts/python.exe", "bin/python"]:
                add_candidate(env_path / bin_name, "venv", f"{env_path.name} (active)")

        # 2. Local workspace directory scan
        common_venv_names = [".venv", "venv", "env", ".env", "virtualenv"]
        for name in common_venv_names:
            candidate_dir = target / name
            if candidate_dir.is_dir():
                for bin_name in ["Scripts/python.exe", "bin/python"]:
                    add_candidate(candidate_dir / bin_name, "venv", candidate_dir.name)

        # 3. Current host sys.executable
        host_bin = Path(sys.executable)
        add_candidate(host_bin, "host", "host python")

        # 4. Global PATH executables
        for tool in ["python", "python3"]:
            which_path = shutil.which(tool)
            if which_path:
                add_candidate(Path(which_path), "system", tool)

        return results

    def build_run_command(self, script_path: str, args: list[str] | None = None) -> str:
        """Generate formatted shell execution command for a Python file."""
        interpreter = self.get_interpreter()
        cmd = f'"{interpreter}" "{script_path}"'
        if args:
            escaped_args = " ".join(f'"{a}"' if " " in a else a for a in args)
            cmd = f"{cmd} {escaped_args}"
        return cmd

    def run_file(self, file_path: str, args: list[str] | None = None, terminal: bool = True) -> dict[str, typing.Any]:
        """Execute Python script in Biscuit terminal or via subprocess runner."""
        cmd = self.build_run_command(file_path, args)
        if terminal and hasattr(self.api, "terminalmanager") and self.api.terminalmanager:
            self.api.terminalmanager.open_terminal(command=cmd)
            self.api.logger.info(f"Launched in terminal: {cmd}")
            return {"command": cmd, "status": "launched", "mode": "terminal"}

        try:
            full_cmd = [self.get_interpreter(), file_path] + (args or [])
            res = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                timeout=60,
            )
            return {
                "command": cmd,
                "status": "completed",
                "returncode": res.returncode,
                "stdout": res.stdout,
                "stderr": res.stderr,
            }
        except Exception as e:
            self.api.logger.error(f"Execution error for {file_path}: {e}")
            return {
                "command": cmd,
                "status": "error",
                "error": str(e),
            }

    def discover_tests(self, test_dir: str | None = None) -> list[dict[str, str]]:
        """Discover pytest tests in directory via pytest collect or AST scanner."""
        target_dir = Path(test_dir or os.getcwd())
        tests: list[dict[str, str]] = []

        # Try pytest --collect-only
        try:
            res = subprocess.run(
                [self.get_interpreter(), "-m", "pytest", "--collect-only", "-q", str(target_dir)],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if res.stdout:
                parsed = self.parse_pytest_collect_output(res.stdout)
                if parsed:
                    return parsed
        except Exception:
            pass

        # Fallback AST static analysis discovery
        for py_file in target_dir.rglob("*.py"):
            if not (py_file.name.startswith("test_") or py_file.name.endswith("_test.py")):
                continue
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                tree = ast.parse(content, filename=str(py_file))
                rel_path = str(py_file.relative_to(target_dir)) if target_dir in py_file.parents else str(py_file)

                for node in tree.body:
                    if isinstance(node, ast.FunctionDef) and node.name.startswith("test"):
                        tests.append({
                            "id": f"{rel_path}::{node.name}",
                            "file": str(py_file),
                            "name": node.name,
                            "class": "",
                        })
                    elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                        for item in node.body:
                            if isinstance(item, ast.FunctionDef) and item.name.startswith("test"):
                                tests.append({
                                    "id": f"{rel_path}::{node.name}::{item.name}",
                                    "file": str(py_file),
                                    "name": item.name,
                                    "class": node.name,
                                })
            except Exception:
                continue

        return tests

    def parse_pytest_collect_output(self, output: str) -> list[dict[str, str]]:
        """Parse pytest --collect-only -q output into structured test items."""
        items: list[dict[str, str]] = []
        for line in output.splitlines():
            clean = line.strip()
            if not clean or " " in clean or clean.startswith("<") or "warning" in clean.lower():
                continue
            if "::" in clean:
                parts = clean.split("::")
                file_part = parts[0]
                class_part = parts[1] if len(parts) > 2 else ""
                name_part = parts[-1]
                items.append({
                    "id": clean,
                    "file": file_part,
                    "class": class_part,
                    "name": name_part,
                })
        return items

    def parse_pytest_output(self, output: str) -> dict[str, typing.Any]:
        """Parse pytest execution stdout/stderr into structured test summary."""
        result: dict[str, typing.Any] = {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "errors": 0,
            "duration": 0.0,
            "tests": [],
        }

        # Parse individual test status lines (e.g. tests/test_foo.py::test_bar PASSED [100%])
        line_regex = re.compile(r"^(\S+::\S+)\s+(PASSED|FAILED|SKIPPED|ERROR)", re.MULTILINE)
        for match in line_regex.finditer(output):
            node_id, status = match.groups()
            result["tests"].append({"id": node_id, "status": status})

        # Summary line pattern: e.g. "== 5 passed, 1 failed, 2 skipped in 1.23s =="
        summary_match = re.search(r"=+ (.*?) in ([\d\.]+)s", output)
        if summary_match:
            stats_str = summary_match.group(1)
            result["duration"] = float(summary_match.group(2))

            passed_m = re.search(r"(\d+)\s+passed", stats_str)
            if passed_m:
                result["passed"] = int(passed_m.group(1))

            failed_m = re.search(r"(\d+)\s+failed", stats_str)
            if failed_m:
                result["failed"] = int(failed_m.group(1))

            skipped_m = re.search(r"(\d+)\s+skipped", stats_str)
            if skipped_m:
                result["skipped"] = int(skipped_m.group(1))

            errors_m = re.search(r"(\d+)\s+error", stats_str)
            if errors_m:
                result["errors"] = int(errors_m.group(1))

        result["total"] = result["passed"] + result["failed"] + result["skipped"] + result["errors"]
        if result["total"] == 0 and result["tests"]:
            result["total"] = len(result["tests"])
            result["passed"] = sum(1 for t in result["tests"] if t["status"] == "PASSED")
            result["failed"] = sum(1 for t in result["tests"] if t["status"] == "FAILED")
            result["skipped"] = sum(1 for t in result["tests"] if t["status"] == "SKIPPED")
            result["errors"] = sum(1 for t in result["tests"] if t["status"] == "ERROR")

        return result

    def run_pytest(
        self,
        test_targets: list[str] | None = None,
        extra_args: list[str] | None = None,
    ) -> dict[str, typing.Any]:
        """Execute pytest with specified targets and return structured results."""
        cmd = [self.get_interpreter(), "-m", "pytest", "-v"]
        if test_targets:
            cmd.extend(test_targets)
        if extra_args:
            cmd.extend(extra_args)

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
            parsed = self.parse_pytest_output(proc.stdout + "\n" + proc.stderr)
            parsed["returncode"] = proc.returncode
            parsed["raw_output"] = proc.stdout
            return parsed
        except Exception as e:
            self.api.logger.error(f"pytest runner error: {e}")
            return {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "skipped": 0,
                "errors": 1,
                "duration": 0.0,
                "tests": [],
                "error": str(e),
                "returncode": -1,
            }

    def install(self) -> None:
        """Register commands, statusbar widget, and editor bindings."""
        # 1. Register commands
        self.api.commands.register_command("python.run_file", lambda: self.run_file_command())
        self.api.commands.register_command("python.select_interpreter", lambda: self.select_interpreter_command())
        self.api.commands.register_command("python.discover_tests", lambda: self.discover_tests())
        self.api.commands.register_command("python.run_tests", lambda: self.run_pytest())

        # 2. Register core integrations
        self.api.register_run_command("python", f'"{self.get_interpreter()}" {{filename}}')
        self.api.register_comment_prefix("python", "#")

        # 3. Statusbar button
        info = self.get_interpreter_info(self.get_interpreter())
        name = info.get("name", "env")
        ver = info.get("version", "")
        status_text = f"Python {ver} ({name})".strip()

        try:
            self.statusbar_button = self.api.statusbar.add_button(
                text=status_text,
                icon="symbol-method",
                callback=self.select_interpreter_command,
                description="Select Python Interpreter",
            )
            if self.statusbar_button:
                self.statusbar_button.show()
        except Exception as e:
            self.api.logger.warning(f"Could not initialize statusbar button: {e}")

        self.api.logger.info("Python extension installed successfully.")

    def run_file_command(self) -> None:
        """Command handler: execute active editor file."""
        active = getattr(self.api.editors, "active_editor", None)
        path = getattr(active, "path", None)
        if path:
            self.run_file(str(path))
        else:
            self.api.notifications.warning("No active Python file open to run.")

    def select_interpreter_command(self) -> list[dict[str, str]]:
        """Command handler: return discovered interpreters."""
        venvs = self.detect_virtualenvs()
        self.api.notifications.info(f"Discovered {len(venvs)} Python environment(s).")
        return venvs
