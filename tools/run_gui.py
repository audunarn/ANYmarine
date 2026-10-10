"""Launch the installed ANYmarine application (no source-path injection)."""
import argparse
import sys

def main():
    parser = argparse.ArgumentParser(description='ANYmarine development desktop')
    parser.add_argument('--project', help='Saved project directory to open')
    parser.add_argument('--viewer-backend', choices=('software', 'auto', 'gpu'),
                        help='ANY3dView renderer; defaults to GPU-preferred auto or ANYMARINE_VIEWER_BACKEND')
    args = parser.parse_args()
    try:
        from anymarine.qtapp import run_app
    except ImportError as error:
        print(f'ANYmarine GUI dependencies are unavailable: {error}. '
              'Install ANYmarine[gui] and the tested ANY3dView Qt wheel into this Python environment.',file=sys.stderr)
        return 2
    return run_app(args.project, viewer_backend=args.viewer_backend)

if __name__ == '__main__':
    raise SystemExit(main())
