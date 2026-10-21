"""
High-Speed VFS Directory Scanner.
Scans workspace hierarchies using os.scandir, filters junk folders (.git, node_modules, target, etc.),
and provides compact hierarchical tree representations.
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional, Set

DEFAULT_IGNORE_DIRS: Set[str] = {
    ".git",
    ".svn",
    ".hg",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".benchmarks",
    ".mypy_cache",
    ".venv",
    ".venv312",
    "venv",
    "env",
    "target",
    "dist",
    "build",
    ".idea",
    ".vscode",
}

DEFAULT_IGNORE_EXTS: Set[str] = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".so",
    ".dll",
    ".dylib",
    ".exe",
    ".bin",
    ".o",
    ".a",
}


@dataclass(slots=True)
class VfsNode:
    """A file or directory entry in the VFS scan tree."""
    path: str
    name: str
    is_dir: bool
    size: int = 0
    extension: str = ""
    children: List["VfsNode"] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class VfsSummary:
    """Aggregated metrics and serialized tree from a VFS scan."""
    root_path: str
    total_files: int
    total_dirs: int
    total_bytes: int
    folded_count: int
    tree_text: str
    all_files: List[str] = field(default_factory=list)


class VfsScanner:
    """Fast directory crawler with automatic junk directory filtering and bounded tree generation."""

    def __init__(self, ignore_dirs: Optional[Set[str]] = None, ignore_exts: Optional[Set[str]] = None):
        self.ignore_dirs = set(ignore_dirs) if ignore_dirs is not None else DEFAULT_IGNORE_DIRS
        self.ignore_exts = set(ignore_exts) if ignore_exts is not None else DEFAULT_IGNORE_EXTS

    def scan(
        self,
        root_path: str = ".",
        max_depth: int = 10,
        max_files: int = 5000,
        render_tree_entries: int = 300,
    ) -> VfsSummary:
        resolved_root = os.path.realpath(root_path)
        total_files = 0
        total_dirs = 0
        total_bytes = 0
        all_files: List[str] = []
        tree_lines: List[str] = []
        folded_count = 0

        def crawl(curr_dir: str, depth: int, prefix: str = "") -> None:
            nonlocal total_files, total_dirs, total_bytes, folded_count
            if depth > max_depth or total_files >= max_files:
                folded_count += 1
                return

            try:
                with os.scandir(curr_dir) as it:
                    entries = list(it)
            except (OSError, PermissionError):
                return

            # Sort: directories first, then files
            entries.sort(key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))

            for idx, entry in enumerate(entries):
                if total_files >= max_files:
                    folded_count += 1
                    continue

                name = entry.name
                if entry.is_dir(follow_symlinks=False):
                    if name in self.ignore_dirs or name.startswith("."):
                        continue
                    total_dirs += 1
                    is_last = (idx == len(entries) - 1)
                    connector = "└── " if is_last else "├── "
                    if len(tree_lines) < render_tree_entries:
                        tree_lines.append(f"{prefix}{connector}{name}/")
                    sub_prefix = prefix + ("    " if is_last else "│   ")
                    crawl(entry.path, depth + 1, sub_prefix)
                else:
                    ext = os.path.splitext(name)[1].lower()
                    if ext in self.ignore_exts:
                        continue
                    try:
                        sz = entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        sz = 0

                    total_files += 1
                    total_bytes += sz
                    rel_path = os.path.relpath(entry.path, resolved_root)
                    all_files.append(rel_path)

                    is_last = (idx == len(entries) - 1)
                    connector = "└── " if is_last else "├── "
                    if len(tree_lines) < render_tree_entries:
                        tree_lines.append(f"{prefix}{connector}{name} ({sz}B)")

        tree_lines.append(f"📁 {os.path.basename(resolved_root) or resolved_root}/")
        crawl(resolved_root, depth=0)

        if folded_count > 0:
            tree_lines.append(f"[... +{folded_count} items folded]")

        return VfsSummary(
            root_path=resolved_root,
            total_files=total_files,
            total_dirs=total_dirs,
            total_bytes=total_bytes,
            folded_count=folded_count,
            tree_text="\n".join(tree_lines),
            all_files=all_files,
        )
