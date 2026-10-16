"""`toolkit <command>`: subcommands discovered from toolkit/commands/*.py.

Module foo_bar is subcommand foo-bar. Each module defines add_arguments(parser) and run(args) -> int
and may add its own sub-subcommands (setting `_command` to the words, for example
'dataset export'). Exit codes: 0 ok, 1 a check failed, 2 usage or input error.

Under MP_QUIET=1 every run ends in exactly one summary line on stdout (toolkit/quiet.py), also for
argparse errors and uncaught exceptions (status error, exit 2, a message with the exception type
and module:line, never the exception text).
"""

from __future__ import annotations

import argparse
import importlib
import pkgutil
import sys
import traceback

from toolkit import quiet
from toolkit.engagement import EngagementError
from toolkit.secrets import SecretError


class UsageError(Exception):
    exit_code = 2


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        if quiet.is_quiet():
            self.print_usage(sys.stderr)
            print(f"{self.prog}: error: {message}", file=sys.stderr)
            raise UsageError("usage")
        super().error(message)


def command_modules() -> list[str]:
    import toolkit.commands as pkg

    return sorted(
        m.name for m in pkgutil.iter_modules(pkg.__path__) if not m.name.startswith("_")
    )


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="toolkit", description="Modernization pipeline toolkit")
    sub = parser.add_subparsers(
        dest="_subcommand", metavar="<command>", parser_class=_Parser
    )
    sub.required = True
    for name in command_modules():
        module = importlib.import_module(f"toolkit.commands.{name}")
        cmd = name.replace("_", "-")
        doc = (module.__doc__ or "").strip().splitlines()
        p = sub.add_parser(
            cmd, help=doc[0] if doc else None, description=module.__doc__
        )
        p.set_defaults(_command=cmd, _run=module.run)
        module.add_arguments(p)
    return parser


def _guess_command(argv: list[str]) -> str:
    names = {n.replace("_", "-") for n in command_modules()}
    for arg in argv:
        if not arg.startswith("-"):
            return arg if arg in names else "toolkit"
    return "toolkit"


def _where(exc: BaseException) -> str:
    """module:line of the innermost toolkit frame (or the innermost frame)."""
    frames = traceback.extract_tb(exc.__traceback__)
    chosen = None
    for frame in frames:
        if "/toolkit/" in frame.filename.replace("\\", "/"):
            chosen = frame
    if chosen is None and frames:
        chosen = frames[-1]
    if chosen is None:
        return "unknown:0"
    path = chosen.filename.replace("\\", "/")
    if "/toolkit/" in path:
        mod = "toolkit." + path.rsplit("/toolkit/", 1)[1].removesuffix(".py").replace(
            "/", "."
        )
    else:
        mod = path.rsplit("/", 1)[-1].removesuffix(".py")
    return f"{mod}:{chosen.lineno}"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    quiet.reset()
    quiet.begin()
    command = _guess_command(argv)
    try:
        try:
            parser = build_parser()
            args = parser.parse_args(argv)
        except UsageError as exc:
            return quiet.summary(
                "error",
                command,
                2,
                messages=[f"usage error: UsageError at {_where(exc)}"],
            )
        except SystemExit as exc:  # --help, or argparse outside quiet mode
            code = (
                exc.code
                if isinstance(exc.code, int)
                else (0 if exc.code is None else 2)
            )
            if quiet.is_quiet():
                return quiet.summary(None, command, code)
            return code
        command = getattr(args, "_command", command)
        try:
            code = args._run(args)
        except (EngagementError, SecretError, UsageError) as exc:
            print(f"{command}: {exc}", file=sys.stderr)
            msgs = getattr(exc, "problems", None) or [str(exc)]
            return quiet.summary("error", command, 2, messages=msgs)
        except KeyboardInterrupt:
            return quiet.summary("error", command, 2, messages=["interrupted"])
        except Exception as exc:
            if not quiet.is_quiet():
                raise
            traceback.print_exc(file=sys.stderr)
            return quiet.summary(
                "error", command, 2, messages=[f"{type(exc).__name__} at {_where(exc)}"]
            )
        if not isinstance(code, int):
            code = 0 if code is None else 2
        if quiet.emitted() is None:
            quiet.summary(None, command, code)
        return code
    finally:
        quiet.end()


if __name__ == "__main__":
    raise SystemExit(main())
