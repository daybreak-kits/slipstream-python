from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = API_DIR / "src"


def _run_in_app_dir(code: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
    )


def test_app_imports_and_boots_without_external_pythonpath():
    result = _run_in_app_dir("from app import app; print(app.title)")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


def test_app_imports_chat_and_routing_services():
    code = (
        "import sys, app; "
        "print('l05_service.chat' in sys.modules, "
        "'l05_service.routing' in sys.modules, "
        "'l05_service.probe' in sys.modules, "
        "'litellm' in sys.modules)"
    )
    result = _run_in_app_dir(code)

    assert result.returncode == 0, result.stderr
    # chat delegates candidate resolution to the live routing seam.
    assert result.stdout.strip() == "True True True False"


def test_litellm_import_is_confined_to_the_llm_outlet():
    offenders: list[str] = []
    pattern = re.compile(r"^\s*(?:import litellm|from litellm\b)", re.MULTILINE)
    for path in SRC_DIR.rglob("*.py"):
        relative = path.relative_to(SRC_DIR)
        if relative.parts[:2] == ("l05_service", "llm"):
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(str(relative))

    assert offenders == []
