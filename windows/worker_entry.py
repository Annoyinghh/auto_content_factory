"""Background entry point bundled alongside the native workbench."""

import sys

from studio_entry import run_command


if __name__ == "__main__":
    code = run_command()
    sys.exit(2 if code is None else code)
