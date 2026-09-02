from __future__ import annotations

import json
import math
import re
from typing import Any


class JSONContractValidationError(ValueError):
    """One JSON value does not satisfy the application's canonical JSON contract."""


def _json_token(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return repr(value)


def _json_equal(left: Any, right: Any) -> bool:
    return _json_token(left) == _json_token(right)


def _child_path(path: str, key: Any) -> str:
    if isinstance(key, int):
        return f"{path}[{key}]"
    text = str(key)
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text):
        return f"{path}.{text}"
    return f"{path}[{json.dumps(text, ensure_ascii=False)}]"


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def _first_error(value: Any, schema: Any, path: str) -> str | None:
    if schema is True:
        return None
    if schema is False:
        return f"{path}: schema rejects every value"
    if not isinstance(schema, dict):
        return f"{path}: invalid json_contract schema node"

    for child_schema in schema.get("allOf") or []:
        error = _first_error(value, child_schema, path)
        if error:
            return error

    any_of = schema.get("anyOf")
    if isinstance(any_of, list) and any_of:
        errors = [_first_error(value, child_schema, path) for child_schema in any_of]
        if all(error is not None for error in errors):
            return f"{path}: value does not satisfy anyOf ({'; '.join(str(error) for error in errors[:3])})"

    one_of = schema.get("oneOf")
    if isinstance(one_of, list) and one_of:
        matches = sum(_first_error(value, child_schema, path) is None for child_schema in one_of)
        if matches != 1:
            return f"{path}: value must satisfy exactly one oneOf branch; matched={matches}"

    not_schema = schema.get("not")
    if isinstance(not_schema, (dict, bool)) and _first_error(value, not_schema, path) is None:
        return f"{path}: value matches forbidden not schema"

    if "const" in schema and not _json_equal(value, schema["const"]):
        return f"{path}: value {_json_token(value)} does not equal const {_json_token(schema['const'])}"

    enum = schema.get("enum")
    if isinstance(enum, list) and not any(_json_equal(value, candidate) for candidate in enum):
        return f"{path}: value {_json_token(value)} is not one of {_json_token(enum)}"

    expected_type = schema.get("type")
    if expected_type is not None:
        expected_types = expected_type if isinstance(expected_type, list) else [expected_type]
        normalized_types = [str(item) for item in expected_types]
        if not any(_matches_type(value, item) for item in normalized_types):
            return f"{path}: expected type {'|'.join(normalized_types)}, got {type(value).__name__}"

    if isinstance(value, dict):
        try:
            min_properties = int(schema.get("minProperties", 0))
        except (TypeError, ValueError):
            min_properties = 0
        if len(value) < min_properties:
            return f"{path}: object has {len(value)} properties, minimum is {min_properties}"
        if "maxProperties" in schema:
            try:
                max_properties = int(schema["maxProperties"])
            except (TypeError, ValueError):
                max_properties = len(value)
            if len(value) > max_properties:
                return f"{path}: object has {len(value)} properties, maximum is {max_properties}"

        required = schema.get("required") or []
        if isinstance(required, list):
            for key in required:
                if key not in value:
                    return f"{_child_path(path, key)}: required property is missing"

        properties = schema.get("properties") or {}
        if not isinstance(properties, dict):
            properties = {}
        for key, child_schema in properties.items():
            if key not in value:
                continue
            error = _first_error(value[key], child_schema, _child_path(path, key))
            if error:
                return error

        pattern_properties = schema.get("patternProperties") or {}
        if not isinstance(pattern_properties, dict):
            pattern_properties = {}
        matched_by_pattern: set[str] = set()
        for pattern, child_schema in pattern_properties.items():
            try:
                compiled = re.compile(str(pattern))
            except re.error:
                continue
            for key, child_value in value.items():
                if compiled.search(str(key)) is None:
                    continue
                matched_by_pattern.add(str(key))
                error = _first_error(child_value, child_schema, _child_path(path, key))
                if error:
                    return error

        extras = [
            key
            for key in value
            if key not in properties and str(key) not in matched_by_pattern
        ]
        additional = schema.get("additionalProperties", True)
        if additional is False and extras:
            return f"{_child_path(path, extras[0])}: additional property is not allowed"
        if isinstance(additional, dict):
            for key in extras:
                error = _first_error(value[key], additional, _child_path(path, key))
                if error:
                    return error

    if isinstance(value, list):
        if "minItems" in schema:
            try:
                minimum = int(schema["minItems"])
            except (TypeError, ValueError):
                minimum = 0
            if len(value) < minimum:
                return f"{path}: array has {len(value)} items, minimum is {minimum}"
        if "maxItems" in schema:
            try:
                maximum = int(schema["maxItems"])
            except (TypeError, ValueError):
                maximum = len(value)
            if len(value) > maximum:
                return f"{path}: array has {len(value)} items, maximum is {maximum}"
        if schema.get("uniqueItems") is True:
            seen: set[str] = set()
            for index, item in enumerate(value):
                token = _json_token(item)
                if token in seen:
                    return f"{_child_path(path, index)}: duplicate array item violates uniqueItems"
                seen.add(token)

        items_schema = schema.get("items")
        if isinstance(items_schema, dict) or isinstance(items_schema, bool):
            for index, item in enumerate(value):
                error = _first_error(item, items_schema, _child_path(path, index))
                if error:
                    return error
        elif isinstance(items_schema, list):
            for index, child_schema in enumerate(items_schema[: len(value)]):
                error = _first_error(value[index], child_schema, _child_path(path, index))
                if error:
                    return error

        contains_schema = schema.get("contains")
        if isinstance(contains_schema, (dict, bool)):
            match_count = sum(
                _first_error(item, contains_schema, _child_path(path, index)) is None
                for index, item in enumerate(value)
            )
            try:
                minimum_contains = int(schema.get("minContains", 1))
            except (TypeError, ValueError):
                minimum_contains = 1
            if match_count < minimum_contains:
                return f"{path}: contains matched {match_count} items, minimum is {minimum_contains}"
            if "maxContains" in schema:
                try:
                    maximum_contains = int(schema["maxContains"])
                except (TypeError, ValueError):
                    maximum_contains = match_count
                if match_count > maximum_contains:
                    return f"{path}: contains matched {match_count} items, maximum is {maximum_contains}"

    if isinstance(value, str):
        if "minLength" in schema:
            try:
                minimum = int(schema["minLength"])
            except (TypeError, ValueError):
                minimum = 0
            if len(value) < minimum:
                return f"{path}: string length {len(value)} is below minimum {minimum}"
        if "maxLength" in schema:
            try:
                maximum = int(schema["maxLength"])
            except (TypeError, ValueError):
                maximum = len(value)
            if len(value) > maximum:
                return f"{path}: string length {len(value)} exceeds maximum {maximum}"
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            try:
                matched = re.search(pattern, value) is not None
            except re.error:
                matched = True
            if not matched:
                return f"{path}: string does not match required pattern {pattern!r}"

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            return f"{path}: number must be finite"
        if "minimum" in schema and value < schema["minimum"]:
            return f"{path}: number {value} is below minimum {schema['minimum']}"
        if "maximum" in schema and value > schema["maximum"]:
            return f"{path}: number {value} exceeds maximum {schema['maximum']}"
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            return f"{path}: number {value} must be greater than {schema['exclusiveMinimum']}"
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            return f"{path}: number {value} must be less than {schema['exclusiveMaximum']}"

    return None


def validate_json_contract(value: Any, schema: Any) -> None:
    """Validate provider output against the canonical application-side JSON contract.

    The provider may receive a transport-normalized schema, but application correctness
    is always decided against the untouched canonical contract. This function is
    intentionally deterministic and semantic-free: it checks JSON structure only.
    """

    error = _first_error(value, schema, "$")
    if error:
        raise JSONContractValidationError(error)


__all__ = ["JSONContractValidationError", "validate_json_contract"]
