"""
Upstream EBNF / GBNF Grammar Exporter.
Compiles JSON Schemas of the Fixed 8 Meta-Tools into deterministic GBNF rules
for upstream LLM logit masking and constrained decoding.
"""

from typing import Any, Dict, List
from onyxsi.gateway.meta_router import MetaToolSpec


class GrammarExporter:
    """Exports MCP tool JSON Schemas into GBNF grammar specifications."""

    @classmethod
    def export_tool_gbnf(cls, spec: MetaToolSpec) -> str:
        rule_prefix = spec.name.replace("_", "-")
        props = spec.input_schema.get("properties", {})
        required = spec.input_schema.get("required", [])

        lines: List[str] = []
        field_rules: List[str] = []

        for prop_name, prop_schema in props.items():
            prop_rule_name = f"{rule_prefix}-{prop_name.replace('_', '-')}"
            val_expr = cls._schema_type_to_gbnf(prop_schema)
            lines.append(f'{prop_rule_name} ::= "\\"{prop_name}\\"" ws ":" ws {val_expr}')
            if prop_name in required:
                field_rules.append(prop_rule_name)
            else:
                field_rules.append(f"({prop_rule_name})?")

        joined_fields = ' ("," ws '.join(field_rules) + (")" * max(0, len(field_rules) - 1)) if field_rules else ""
        args_rule = f'{rule_prefix}-args ::= "{{" ws {joined_fields} ws "}}"'
        call_rule = (
            f'{rule_prefix}-call ::= "{{" ws "\\"name\\"" ws ":" ws "\\"{spec.name}\\"" '
            f'ws "," ws "\\"arguments\\"" ws ":" ws {rule_prefix}-args ws "}}"'
        )
        lines.insert(0, args_rule)
        lines.insert(0, call_rule)
        return "\n".join(lines)

    @classmethod
    def export_all_meta_tools_gbnf(cls, specs: List[MetaToolSpec]) -> str:
        header = [
            "root ::= tool-call",
            "tool-call ::= " + " | ".join(f"{s.name.replace('_', '-')}-call" for s in specs),
            'ws ::= [ \\t\\n]*',
            'string ::= "\\"" ([^"\\\\] | "\\\\" .)* "\\""',
            'integer ::= "-"? [0-9]+',
            'number ::= "-"? [0-9]+ ("." [0-9]+)?',
            'boolean ::= "true" | "false"',
            'json-object ::= "{" ws (string ws ":" ws json-value ("," ws string ws ":" ws json-value)*)? ws "}"',
            'json-array ::= "[" ws (json-value ("," ws json-value)*)? ws "]"',
            'json-value ::= string | number | boolean | json-object | json-array | "null"',
        ]
        body = [cls.export_tool_gbnf(spec) for spec in specs]
        return "\n\n".join(["\n".join(header)] + body)

    @staticmethod
    def _schema_type_to_gbnf(prop_schema: Dict[str, Any]) -> str:
        if "enum" in prop_schema:
            opts = [f'"\\"{v}\\""' for v in prop_schema["enum"]]
            return "(" + " | ".join(opts) + ")"
        t = prop_schema.get("type", "string")
        if t == "integer":
            return "integer"
        if t == "number":
            return "number"
        if t == "boolean":
            return "boolean"
        if t == "array":
            return "json-array"
        if t == "object":
            return "json-object"
        return "string"
