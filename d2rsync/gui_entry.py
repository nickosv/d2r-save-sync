"""Frozen-app entry. Importing the window here keeps Qt in the PyInstaller bundle.

With no arguments the window opens. Any other argument is a CLI subcommand.
"""

from __future__ import annotations

import sys

import d2rsync.ui.app  # noqa: F401  (PyInstaller follows this into PySide6)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] == "gui":
        from d2rsync.ui.app import run

        return run()
    from d2rsync.cli import main as cli_main

    return cli_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
