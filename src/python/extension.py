from __future__ import annotations

import os
import sys
import typing

from biscuit.extensions import Extension

if typing.TYPE_CHECKING:
    from biscuit.api import ExtensionsAPI


class Python(Extension):
    """Python language support, virtual environment manager, and script runner."""

    def __init__(self, api: ExtensionsAPI) -> None:
        super().__init__(api)
        self.api = api
        self.api.logger.info("Python extension initialised.")

    def get_interpreter(self) -> str:
        """Return current Python interpreter executable."""
        return sys.executable

    def detect_virtualenvs(self, base_dir: str | None = None) -> list[str]:
        """Scan for virtual environments in the target directory."""
        target = base_dir or os.getcwd()
        found = []
        for name in [".venv", "venv", "env", ".env"]:
            candidate = os.path.join(target, name)
            if os.path.isdir(candidate):
                found.append(candidate)
        return found

    def build_run_command(self, script_path: str) -> str:
        """Generate execution command for a Python file."""
        return f'"{sys.executable}" "{script_path}"'

    def install(self) -> None:
        self.api.logger.info("Python extension installed successfully.")
