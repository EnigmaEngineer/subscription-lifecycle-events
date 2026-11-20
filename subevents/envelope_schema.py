"""Check an envelope against the schema that gets registered in Glue.

The registry holds a schema and nothing in this repository was reading it, which
is the shape where a declared extent passes for coverage. A schema registered
and never evaluated tells a consumer what to expect and tells the producer
nothing at all.

This implements the part of draft-07 the schema file actually uses and no more.

    type            including the two element union used for api_version
    required
    minLength
    properties      recursively, on objects

An unknown keyword is ignored rather than refused, which is stated here because
it is the difference between a checker and a validator. If the schema grows a
keyword this does not implement, `test_envelope_schema.py` fails on the keyword
list rather than passing over it in silence.

The point of the comparison is not that the schema agrees with the handler. It
is to find out where it does not, because the handler reads four fields and the
schema describes more than four.
"""

from __future__ import annotations

import json
import os

SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "schemas",
    "subscription-event.json",
)

IMPLEMENTED_KEYWORDS = frozenset(
    {"$schema", "title", "description", "type", "required", "properties", "minLength"}
)

# Every type name this checker answers for. Asserted against the names the
# schema file actually uses, because a type name it does not implement would
# otherwise fall through _type_matches and read as a violation of everything.
IMPLEMENTED_TYPES = frozenset(
    {"object", "array", "string", "number", "integer", "boolean", "null"}
)

JSON_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


def load_schema(path: str = SCHEMA_PATH) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def types_used(schema: dict) -> set[str]:
    """Every type name named anywhere in the schema, including inside a union."""
    found: set[str] = set()
    if not isinstance(schema, dict):
        return found
    declared = schema.get("type")
    if isinstance(declared, str):
        found.add(declared)
    elif isinstance(declared, list):
        found |= {name for name in declared if isinstance(name, str)}
    for key, value in schema.items():
        if key == "properties" and isinstance(value, dict):
            for sub in value.values():
                found |= types_used(sub)
        elif isinstance(value, dict):
            found |= types_used(value)
    return found


def keywords_used(schema: dict) -> set[str]:
    """Every keyword anywhere in the schema, so an unimplemented one is visible."""
    found: set[str] = set()
    if not isinstance(schema, dict):
        return found
    for key, value in schema.items():
        found.add(key)
        if key == "properties" and isinstance(value, dict):
            for sub in value.values():
                found |= keywords_used(sub)
        elif isinstance(value, dict):
            found |= keywords_used(value)
    return found


def _type_matches(value: object, expected: object) -> bool:
    names = expected if isinstance(expected, list) else [expected]
    for name in names:
        if name == "integer":
            # bool is an int in Python and is not an integer in JSON Schema.
            # The handler learned this on 2026-11-18 from a fixture carrying
            # created as true.
            if isinstance(value, int) and not isinstance(value, bool):
                return True
        elif name == "boolean":
            if isinstance(value, bool):
                return True
        elif name == "number":
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return True
        elif name in JSON_TYPES:
            # No bool exclusion here. A bool is only ever an instance of int,
            # and integer and number both answer above this line, so the guard
            # that used to sit here could not change an outcome. A mutation pass
            # on 2026-11-20 is what showed that, by surviving.
            if isinstance(value, JSON_TYPES[name]):
                return True
    return False


def violations(document: object, schema: dict | None = None, path: str = "") -> list[str]:
    """Every way the document departs from the schema, as readable paths."""
    schema = load_schema() if schema is None else schema
    out: list[str] = []

    expected = schema.get("type")
    if expected is not None and not _type_matches(document, expected):
        out.append(f"{path or 'envelope'} is not {expected}")
        return out

    if isinstance(document, str) and "minLength" in schema:
        if len(document) < schema["minLength"]:
            out.append(f"{path or 'envelope'} is shorter than {schema['minLength']}")

    if isinstance(document, dict):
        for field in schema.get("required", []):
            if field not in document:
                out.append(f"{path}{'.' if path else ''}{field} is missing")
        for field, sub in schema.get("properties", {}).items():
            if field in document:
                child = f"{path}{'.' if path else ''}{field}"
                out.extend(violations(document[field], sub, child))

    return out


def conforms(document: object, schema: dict | None = None) -> bool:
    return not violations(document, schema)
