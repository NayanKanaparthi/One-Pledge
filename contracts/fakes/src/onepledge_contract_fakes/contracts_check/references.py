"""References, operations and the validator boundary of the contract checker (SPEC_DECISIONS 111).

Part of the contract checker in the TEST DOUBLES package `onepledge_contract_fakes`.

1. Before any library sees a contract file, every `$ref` in it is resolved here: it must be
   local (`#/...`) and point at an existing node. Each one that does not is one problem naming
   the place of the reference as a JSON pointer.
2. The third party validators are called behind `contained`, the one broad catch of layer 1.
3. Every example an operation names (`externalValue` in a request body or a response) is
   validated against the schema reached through that operation.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from onepledge_contract_fakes.validation import parse_integer_text

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


def _escape(part: object) -> str:
    return str(part).replace("~", "~0").replace("/", "~1")


def _unescape(part: str) -> str:
    return part.replace("~1", "/").replace("~0", "~")


def _references(node: object, pointer: str) -> Iterator[tuple[str, object]]:
    if isinstance(node, Mapping):
        if "$ref" in node:
            yield pointer, node["$ref"]
        for key, value in node.items():
            yield from _references(value, f"{pointer}/{_escape(key)}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from _references(item, f"{pointer}/{index}")


def resolve(document: Mapping[str, Any], reference: str) -> object | None:
    """The node a local reference points at, or None when it points at nothing."""
    node: object = document
    for part in reference.removeprefix("#/").split("/") if reference != "#" else []:
        key = _unescape(part)
        if isinstance(node, Mapping) and key in node:
            node = node[key]
            continue
        # A JSON pointer array index is `0` or digits without a leading zero (RFC 6901).
        canonical = key == "0" or not key.startswith("0")
        listed = isinstance(node, list) and bool(node) and canonical
        index = parse_integer_text(key, 0, len(node) - 1) if listed and isinstance(node, list) else None
        if not isinstance(index, int) or not isinstance(node, list):
            return None
        node = node[index]
    return node


def reference_problems(name: str, document: Mapping[str, Any]) -> list[str]:
    """One problem per `$ref` of the file that is not local or does not resolve."""
    problems = []
    for pointer, reference in _references(document, ""):
        where = pointer or "/"
        if not isinstance(reference, str) or not (reference == "#" or reference.startswith("#/")):
            problems.append(f"{name}: the reference at {where} names another file")
        elif resolve(document, reference) is None:
            problems.append(f"{name}: the reference at {where} does not resolve")
    return problems


def contained(name: str, validate: Callable[[], list[str]]) -> list[str]:
    """Run a third party validator; anything it raises becomes one problem of the file.

    This is the one place in layer 1 where a broad catch is allowed (SPEC_DECISIONS 111.2):
    the exception classes of the validator libraries are not a stable interface. No class
    name is printed.
    """
    try:
        return validate()
    except Exception:  # The validator boundary of SPEC_DECISIONS 111.2.
        return [f"{name}: the validator could not check this file"]


@dataclass(frozen=True)
class OperationExample:
    """An example file an operation names, with the schema it must satisfy."""

    file: str
    where: str
    schema: object


def operation_examples(document: Mapping[str, Any]) -> list[OperationExample]:
    """Every `externalValue` under an operation's request body or responses, with its schema."""
    found: list[OperationExample] = []
    paths = document.get("paths", {})
    for path, item in paths.items() if isinstance(paths, Mapping) else ():
        for method in HTTP_METHODS:
            operation = item.get(method) if isinstance(item, Mapping) else None
            if not isinstance(operation, Mapping):
                continue
            label = f"{method.upper()} {path}"
            body = _follow(document, operation.get("requestBody"))
            found.extend(_media_examples(document, body, f"{label} request"))
            responses = operation.get("responses", {})
            for status, response in responses.items() if isinstance(responses, Mapping) else ():
                found.extend(_media_examples(document, _follow(document, response), f"{label} {status}"))
    return found


def _follow(document: Mapping[str, Any], node: object) -> object:
    if isinstance(node, Mapping) and isinstance(node.get("$ref"), str):
        return resolve(document, node["$ref"])
    return node


def _media_examples(document: Mapping[str, Any], holder: object, where: str) -> list[OperationExample]:
    content = holder.get("content") if isinstance(holder, Mapping) else None
    found: list[OperationExample] = []
    for media in content.values() if isinstance(content, Mapping) else ():
        examples = media.get("examples") if isinstance(media, Mapping) else None
        for entry in examples.values() if isinstance(examples, Mapping) else ():
            example = _follow(document, entry)
            if isinstance(example, Mapping) and isinstance(example.get("externalValue"), str):
                found.append(OperationExample(example["externalValue"], where, media.get("schema")))
    return found
