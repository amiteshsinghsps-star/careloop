"""schema — just enough JSON Schema to prove a tools/call result matches the declared
outputSchema (type/required/properties/items/enum). Extra keys are allowed: MCP servers
attach policy fields the schema need not enumerate.
"""


def _type_ok(value, want):
    if want == "object":
        return isinstance(value, dict)
    if want == "array":
        return isinstance(value, list)
    if want == "string":
        return isinstance(value, str)
    if want == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if want == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if want == "boolean":
        return isinstance(value, bool)
    if want == "null":
        return value is None
    return True


def validate(value, schema, path="result"):
    """Return a list of human-readable violations; empty means the value conforms."""
    if not isinstance(schema, dict) or not schema:
        return []
    bad = []
    types = schema.get("type")
    if types is not None:
        wanted = types if isinstance(types, list) else [types]
        if not any(_type_ok(value, t) for t in wanted):
            bad.append("%s: expected %s, got %s" % (path, "/".join(wanted), type(value).__name__))
            return bad
    if "enum" in schema and value not in schema["enum"]:
        bad.append("%s: %r is not one of %s" % (path, value, schema["enum"]))
    if isinstance(value, dict):
        for req in schema.get("required") or []:
            if req not in value:
                bad.append("%s.%s: required property missing" % (path, req))
        props = schema.get("properties") or {}
        for key, sub in props.items():
            if key in value:
                bad.extend(validate(value[key], sub, "%s.%s" % (path, key)))
    if isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for i, v in enumerate(value):
                bad.extend(validate(v, items, "%s[%d]" % (path, i)))
        if "minItems" in schema and len(value) < schema["minItems"]:
            bad.append("%s: fewer than %d items" % (path, schema["minItems"]))
    if isinstance(value, str) and "minLength" in schema and len(value) < schema["minLength"]:
        bad.append("%s: shorter than %d characters" % (path, schema["minLength"]))
    if isinstance(value, int) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            bad.append("%s: %r below minimum %r" % (path, value, schema["minimum"]))
        if "maximum" in schema and value > schema["maximum"]:
            bad.append("%s: %r above maximum %r" % (path, value, schema["maximum"]))
    return bad
