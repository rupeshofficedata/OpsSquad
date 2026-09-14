"""Every tool the registry can serve up must have a declared JSON-schema
(app/tools/schemas.py) — otherwise the model gets a blank/guessable
parameters block again (the original "wrong param name" bug)."""

from app.tools.schemas import TOOL_SCHEMAS
from app.tools.stubs import SIMULATED_TOOLS


def test_every_simulated_tool_has_a_schema():
    missing = set(SIMULATED_TOOLS) - set(TOOL_SCHEMAS)
    assert not missing, f"tools missing a schema entry: {missing}"


def test_every_schema_is_a_valid_object_schema():
    for name, schema in TOOL_SCHEMAS.items():
        assert schema.get("type") == "object", name
        assert isinstance(schema.get("properties"), dict), name
        assert set(schema.get("required", [])) <= set(schema["properties"]), name


def test_kubectl_target_ops_require_target():
    for name in ("kubectl.get", "kubectl.logs", "kubectl.restart", "kubectl.scale"):
        assert "target" in TOOL_SCHEMAS[name]["required"], name
