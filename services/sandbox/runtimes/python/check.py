"""Compile-only check for Python submissions: prints a SyntaxError exactly as
CPython would and exits 1, without executing anything."""

import sys
import traceback


def main() -> int:
    path = sys.argv[1]
    display = sys.argv[2] if len(sys.argv) > 2 else path
    try:
        with open(path, "rb") as fh:
            source = fh.read()
        compile(source, display, "exec", dont_inherit=True)
    except (SyntaxError, ValueError, UnicodeDecodeError) as exc:
        sys.stderr.write("".join(traceback.format_exception_only(type(exc), exc)))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
