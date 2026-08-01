from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.python import setup
from src.python.extension import Python


class TestPythonExtension:
    def test_extension_setup(self, app_instance) -> None:
        setup(app_instance.api)
        ext = Python(app_instance.api)
        assert ext.get_interpreter() == sys.executable
        venvs = ext.detect_virtualenvs()
        assert isinstance(venvs, list)
        cmd = ext.build_run_command("main.py")
        assert "main.py" in cmd
