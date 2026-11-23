"""The argument parser of every `onepledge-bank` command (SPEC_DECISIONS 78, 121).

Arguments are exact: no option can be abbreviated, an option or flag given twice is a usage
error, and a flag takes no value. A usage error prints the usage text and the product's own
sentence on stderr, exit 2: each message of `argparse` is matched by one pattern and
rewritten, the value the operator typed is never repeated, no repr characters appear, and a
message no pattern knows becomes the plain sentence. Help and usage text are at most 100
columns, whatever the terminal says.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable, Sequence
from typing import NoReturn

from onepledge_bank_agent.interface.cli.output import USAGE_STATUS, WIDTH, fit

_USAGE_SENTENCES: tuple[tuple[re.Pattern[str], Callable[[re.Match[str]], str]], ...] = (
    (
        re.compile(r"^argument (?P<name>[^:]+): invalid choice: .*\(choose from (?P<choices>.*)\)$"),
        lambda m: f"argument {m['name']} must be one of: {', '.join(_choices(m['choices']))}",
    ),
    (
        re.compile(r"^argument (?P<name>[^:]+): ignored explicit argument .*$"),
        lambda m: f"argument {m['name']} takes no value",
    ),
    (
        re.compile(r"^argument (?P<name>[^:]+): expected (?:one argument|[0-9]+ arguments?)$"),
        lambda m: f"argument {m['name']} needs a value",
    ),
    (
        re.compile(r"^argument (?P<name>[^:]+): (?P<rest>(?:must|is given) [^'\"()\[\]{}]*)$"),
        lambda m: f"argument {m['name']} {m['rest']}",
    ),
    (
        re.compile(r"^the following arguments are required: (?P<names>[^'\"()\[\]{}]+)$"),
        lambda m: f"these arguments are required: {m['names']}",
    ),
    (
        re.compile(r"^unrecognized arguments: .*$"),
        lambda _: "the command line holds an argument this command does not take",
    ),
)
PLAIN_USAGE_SENTENCE = "the command line is not valid"
_GIVEN = "_options_given"
_CHOICE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


def _choices(text: str) -> list[str]:
    quoted = re.findall(r"'([^']*)'", text)
    found = quoted or [choice.strip() for choice in text.split(",")]
    return [choice for choice in found if _CHOICE_NAME.fullmatch(choice)]


def usage_sentence(message: str) -> str:
    """The product's sentence for one message of the argument parser (SPEC_DECISIONS 121.2)."""
    for pattern, sentence in _USAGE_SENTENCES:
        match = pattern.fullmatch(message)
        if match is not None:
            return sentence(match)
    return PLAIN_USAGE_SENTENCE


def _given_once(namespace: argparse.Namespace, action: argparse.Action, option: str | None) -> None:
    if option is None:
        return
    given: set[str] = vars(namespace).setdefault(_GIVEN, set())
    if action.dest in given:
        raise argparse.ArgumentError(action, "is given twice")
    given.add(action.dest)


class _StoreOnce(argparse.Action):
    """`store`, refusing an option given twice."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[object] | None,
        option_string: str | None = None,
    ) -> None:
        _given_once(namespace, self, option_string)
        setattr(namespace, self.dest, values)


class _FlagOnce(argparse.Action):
    """`store_true`, refusing a flag given twice and any value."""

    def __init__(
        self,
        option_strings: Sequence[str],
        dest: str,
        default: bool = False,
        required: bool = False,
        help: str | None = None,
    ) -> None:
        super().__init__(
            option_strings, dest, nargs=0, const=True, default=default, required=required, help=help
        )

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[object] | None,
        option_string: str | None = None,
    ) -> None:
        _given_once(namespace, self, option_string)
        setattr(namespace, self.dest, True)


class _Formatter(argparse.HelpFormatter):
    """Help and usage text at most 100 columns wide."""

    def __init__(self, prog: str) -> None:
        super().__init__(prog, width=WIDTH)


class ProductParser(argparse.ArgumentParser):
    """No abbreviations, no repeated option, help at most 100 columns, usage errors in the product's
    own words. Subcommand parsers are made of the same class (argparse passes the parser's class on)."""

    def __init__(
        self, prog: str | None = None, description: str | None = None, add_help: bool = True
    ) -> None:
        super().__init__(
            prog=prog,
            description=description,
            add_help=add_help,
            allow_abbrev=False,
            formatter_class=_Formatter,
        )
        self.register("action", None, _StoreOnce)
        self.register("action", "store", _StoreOnce)
        self.register("action", "store_true", _FlagOnce)

    def error(self, message: str) -> NoReturn:
        """The usage text and the product's sentence on stderr, exit 2."""
        self.print_usage(sys.stderr)
        self.exit(USAGE_STATUS, fit(f"{self.prog}: {usage_sentence(message)}.") + "\n")
