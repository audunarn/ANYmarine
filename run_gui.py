"""Run this ANYmarine checkout using a local GUI runtime when available."""
import os
from pathlib import Path
import runpy
import subprocess
import sys
from local_gui_config import local_settings


def main():
    root = Path(__file__).resolve().parent
    try:
        configured, assets = local_settings(root, os.environ)
    except (OSError, ValueError, UnicodeError) as error:
        print(f"ANYmarine local configuration refused: {error}", file=sys.stderr)
        return 2
    if assets is not None:
        os.environ['ANYMARINE_REFERENCE_BARGE_DIR'] = assets
    candidates = [root / ".venv" / "Scripts" / "python.exe",
                  root / ".venv" / "bin" / "python",
                  root.parent / "anymarine-evidence" / "runtime" / "Scripts" / "python.exe"]
    if os.name == "nt":
        candidates.append(Path(root.anchor) / "ANYworkspaceWork" / "anymarine-evidence" / "runtime" / "Scripts" / "python.exe")
    runtime = Path(configured) if configured else next((p for p in candidates if p.is_file()), Path(sys.executable))
    if not runtime.is_file():
        print(f"ANYMARINE_PYTHON does not name an interpreter: {runtime}", file=sys.stderr)
        return 2
    if runtime.resolve() != Path(sys.executable).resolve():
        return subprocess.call([str(runtime), str(Path(__file__).resolve()), *sys.argv[1:]])
    sys.path.insert(0, str(root / "src"))
    runpy.run_path(str(root / "tools" / "run_gui.py"), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
