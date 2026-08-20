"""rmerrata CLI — four subcommands mirroring the original flat scripts.

    rmerrata extract  [arguments for errata_extractor.main]
    rmerrata validate [no extra arguments]
    rmerrata regression [--seed N]
    rmerrata report   [--scan-dir DIR] [--report PATH]

Each subcommand forwards the remaining arguments to the module's own argparse
parser, so `--help` works per subcommand:  `rmerrata extract --help`.
"""

import sys
from typing import Callable, Sequence

from rmerrata import extractor, rag_utils, regression, report, validate

COMMANDS: dict[str, Callable] = {
    "extract": extractor.main,
    "validate": validate.main,
    "regression": regression.main,
    "report": report.main,
    "smoke": rag_utils.main,
}

NOARG = (validate.main, rag_utils.main)

USAGE = "usage: rmerrata {extract|validate|regression|report|smoke} [options]"


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(USAGE)
        print("\ncommands:")
        for name, fn in COMMANDS.items():
            print(f"  {name:<10} {fn.__module__.rsplit('.', 1)[-1]}.{fn.__name__}")
        print("\npaths (module-level constants in rmerrata.extractor):")
        print(f"  INPUT_DIR  = {extractor.INPUT_DIR}   (override: ERRATA_INPUT_DIR)")
        print(f"  OUTPUT_DIR = {extractor.OUTPUT_DIR}  (override: ERRATA_OUTPUT_DIR)")
        return 0
    cmd = args[0]
    if cmd not in COMMANDS:
        print(f"rmerrata: unknown command {cmd!r}", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2
    fn = COMMANDS[cmd]
    if fn in NOARG:
        return fn()
    return fn(args[1:])


if __name__ == "__main__":
    sys.exit(main())