import sys

from .mainwindow import run_app

if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else None
    raise SystemExit(run_app(root))
