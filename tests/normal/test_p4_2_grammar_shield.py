"""
Normal Tests for Phase 4.2: Fast Schema Validator & Deterministic JSON Auto-Repair.
"""

from onyxsi.gateway.grammar_shield import GrammarShield


def test_grammar_shield_valid_and_trailing_comma_repair():
    shield = GrammarShield()

    clean_res = shield.parse_and_repair('{"action": "tree", "target": "src"}')
    assert clean_res.was_repaired is False
    assert clean_res.data["action"] == "tree"

    trailing_res = shield.parse_and_repair('{"action": "skeleton", "target": "app.py", }')
    assert trailing_res.was_repaired is True
    assert trailing_res.data["target"] == "app.py"


def test_grammar_shield_truncated_json_auto_repair():
    shield = GrammarShield()
    truncated = '{"name": "vfs_mutate", "arguments": {"op": "replace_node", "path": "main.py"'
    res = shield.parse_and_repair(truncated)
    assert res.was_repaired is True
    assert res.data["name"] == "vfs_mutate"
    assert res.data["arguments"]["path"] == "main.py"


def test_grammar_shield_type_coercion():
    shield = GrammarShield()
    schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["tree", "skeleton"]},
            "max_tokens": {"type": "integer"},
            "recursive": {"type": "boolean"},
        },
        "required": ["action", "max_tokens"],
    }
    raw_payload = {"action": "tree", "max_tokens": "650", "recursive": "true"}
    res = shield.validate_and_coerce(raw_payload, schema)
    assert res.was_repaired is True
    assert res.data["max_tokens"] == 650
    assert res.data["recursive"] is True
    assert "max_tokens" in res.coerced_fields
