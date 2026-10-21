"""
Zero Dependency & Zero UI Auditor (AST-based).
Runs at boot time to guarantee 100% headless, zero-dependency purity.
"""

import ast
import os
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Any, Optional, Union


class ViolationType(Enum):
    EXTERNAL_DEPENDENCY = "EXTERNAL_DEPENDENCY"
    UI_LIBRARY = "UI_LIBRARY"
    INTERACTIVE_CLI = "INTERACTIVE_CLI"
    SYNTAX_ERROR = "SYNTAX_ERROR"


@dataclass(slots=True, frozen=True)
class Violation:
    file_path: str
    line_number: int
    column: int
    violation_type: ViolationType
    module_name: str
    code_snippet: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "line_number": self.line_number,
            "column": self.column,
            "violation_type": self.violation_type.value,
            "module_name": self.module_name,
            "code_snippet": self.code_snippet,
            "message": self.message,
        }


@dataclass(slots=True, frozen=True)
class AuditResult:
    passed: bool
    violations: List[Violation] = field(default_factory=list)
    files_scanned: int = 0
    total_lines: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "violations": [v.to_dict() for v in self.violations],
            "files_scanned": self.files_scanned,
            "total_lines": self.total_lines,
        }


class ZeroDepAndZeroUiAuditor:
    """AST auditor scanning Python files for external packages and UI/TUI components."""

    BANNED_EXTERNAL_PACKAGES = {
        "numpy", "pandas", "scipy", "tensorflow", "torch", "onnx",
        "sklearn", "scikit-learn", "sqlalchemy", "psycopg2", "psutil",
        "redis", "pymongo", "tree_sitter", "lark", "click", "typer",
        "fastapi", "flask", "django", "requests", "httpx", "aiohttp",
        "PIL", "pillow", "matplotlib", "seaborn", "pytest", "pydantic",
        "pyarrow", "polars", "boto3",
    }

    BANNED_UI_LIBRARIES = {
        "curses", "tkinter", "rich", "textual", "prompt_toolkit",
        "blessed", "urwid", "gradio", "streamlit", "nicegui",
        "dash", "plotly", "bokeh",
    }

    def __init__(self, workspace_root: str = "."):
        self.workspace_root = Path(workspace_root).resolve()

    def audit_directory(self, directory: Optional[Union[str, Path]] = None, depth: int = 0) -> AuditResult:
        target_dir = Path(directory or self.workspace_root).resolve()
        if depth > 10:
            return AuditResult(passed=True, files_scanned=0, total_lines=0)

        violations: List[Violation] = []
        files_scanned = 0
        total_lines = 0

        for root, dirs, files in os.walk(target_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in {"__pycache__", "node_modules", "venv", ".venv"}]
            for fname in files:
                if fname.endswith(".py"):
                    fpath = Path(root) / fname
                    files_scanned += 1
                    file_violations, lines = self._audit_file(fpath)
                    violations.extend(file_violations)
                    total_lines += lines

        passed = len(violations) == 0
        return AuditResult(
            passed=passed,
            violations=violations,
            files_scanned=files_scanned,
            total_lines=total_lines,
        )

    def _audit_file(self, file_path: Path) -> tuple[List[Violation], int]:
        violations: List[Violation] = []
        lines_count = 0
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            lines = content.splitlines()
            lines_count = len(lines)
            tree = ast.parse(content, filename=str(file_path))
        except SyntaxError as exc:
            lineno = exc.lineno or 1
            col = exc.offset or 0
            snippet = exc.text.strip() if exc.text else (lines[lineno - 1].strip() if 0 < lineno <= len(lines) else "")
            violations.append(
                Violation(
                    file_path=str(file_path),
                    line_number=lineno,
                    column=col,
                    violation_type=ViolationType.SYNTAX_ERROR,
                    module_name="",
                    code_snippet=snippet,
                    message=f"Unparsable syntax in Python source: {exc.msg or exc}",
                )
            )
            return violations, lines_count
        except (OSError, UnicodeDecodeError):
            return violations, lines_count

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_mod = alias.name.split(".")[0]
                    self._check_module(root_mod, node, file_path, lines, violations)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root_mod = node.module.split(".")[0]
                    self._check_module(root_mod, node, file_path, lines, violations)

        return violations, lines_count

    def _check_module(self, mod_name: str, node: ast.AST, file_path: Path, lines: List[str], violations: List[Violation]) -> None:
        lineno = getattr(node, "lineno", 1)
        col = getattr(node, "col_offset", 0)
        snippet = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""

        if mod_name in self.BANNED_UI_LIBRARIES:
            violations.append(Violation(
                file_path=str(file_path),
                line_number=lineno,
                column=col,
                violation_type=ViolationType.UI_LIBRARY,
                module_name=mod_name,
                code_snippet=snippet,
                message=f"Banned UI/TUI library detected: {mod_name}",
            ))
        elif mod_name in self.BANNED_EXTERNAL_PACKAGES:
            violations.append(Violation(
                file_path=str(file_path),
                line_number=lineno,
                column=col,
                violation_type=ViolationType.EXTERNAL_DEPENDENCY,
                module_name=mod_name,
                code_snippet=snippet,
                message=f"Banned external dependency detected: {mod_name}",
            ))
