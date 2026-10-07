"""Validate that all file references in package.json and workflow files exist."""
from __future__ import annotations

import json
import ast
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def check_package_json_scripts() -> list[str]:
    """Check all script commands in package.json reference existing files."""
    pkg_path = ROOT / "frontend" / "package.json"
    if not pkg_path.exists():
        return [f"Missing {pkg_path}"]
    
    pkg = json.loads(pkg_path.read_text())
    errors = []
    
    for name, command in pkg.get("scripts", {}).items():
        # Find node script references like "node scripts/foo.mjs"
        for match in re.finditer(r'node\s+(scripts/[^\s&|;]+)', command):
            script_path = ROOT / "frontend" / match.group(1)
            if not script_path.exists():
                errors.append(f"package.json script '{name}': missing {script_path.relative_to(ROOT)}")
        
        # Find python script references
        for match in re.finditer(r'python\s+(scripts/[^\s&|;]+)', command):
            script_path = ROOT / match.group(1)
            if not script_path.exists():
                errors.append(f"package.json script '{name}': missing {script_path.relative_to(ROOT)}")
    
    return errors


def check_workflow_file_references() -> list[str]:
    """Check all run: steps in workflow YAMLs reference existing files."""
    errors = []
    workflow_dir = ROOT / ".github" / "workflows"
    
    for wf_path in workflow_dir.glob("*.yml"):
        content = wf_path.read_text()
        
        # Find python script references
        for match in re.finditer(r'run:\s*python\s+(scripts/[^\s&\|;\n]+)', content):
            script_path = ROOT / match.group(1)
            if not script_path.exists():
                errors.append(f"{wf_path.name}: missing python script {script_path.relative_to(ROOT)}")
        
        # Find node script references
        for match in re.finditer(r'run:\s*node\s+(scripts/[^\s&\|;\n]+)', content):
            script_path = ROOT / match.group(1)
            if not script_path.exists():
                errors.append(f"{wf_path.name}: missing node script {script_path.relative_to(ROOT)}")
        
        # Find bash script references
        for match in re.finditer(r'run:\s*bash\s+(scripts/[^\s&\|;\n]+)', content):
            script_path = ROOT / match.group(1)
            if not script_path.exists():
                errors.append(f"{wf_path.name}: missing bash script {script_path.relative_to(ROOT)}")
    
    return errors


def check_python_imports() -> list[str]:
    """Check that python -m module references in workflows are valid."""
    errors = []
    workflow_dir = ROOT / ".github" / "workflows"
    
    for wf_path in workflow_dir.glob("*.yml"):
        content = wf_path.read_text()
        
        # Find python -m references
        for match in re.finditer(r'python\s+-m\s+([a-zA-Z_][a-zA-Z0-9_.]*)', content):
            module = match.group(1)
            # Try to import the module
            try:
                __import__(module)
            except ImportError:
                errors.append(f"{wf_path.name}: python module '{module}' not importable")
    
    return errors


def main() -> int:
    all_errors = []
    all_errors.extend(check_package_json_scripts())
    all_errors.extend(check_workflow_file_references())
    all_errors.extend(check_python_imports())
    all_errors.extend(check_source_imports())
    
    if all_errors:
        print("Dangling file reference check failed:")
        for err in all_errors:
            print(f"  - {err}")
        return 1
    
    print("All file references valid.")
    return 0


def _module_scope_nodes(tree: ast.AST) -> list[ast.AST]:
    """Conditional exports count; function/class-local bindings do not."""
    pending = [tree]
    declarations = []
    while pending:
        node = pending.pop()
        declarations.append(node)
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            pending.extend(ast.iter_child_nodes(node))
    return declarations


def check_source_imports(root: Path | None = None) -> list[str]:
    """Validate project-local absolute/relative imports without executing modules.

    External and optional dependencies are verified by installation/runtime gates.
    Package directories remain known even when a ZIP lost their __init__.py.
    """
    root = root or ROOT
    excluded = {".venv", "venv", "node_modules", ".git", "__pycache__", ".next", "cache", "logs", "database", "local-history"}
    sources: list[Path] = []
    for directory, children, files in os.walk(root):
        children[:] = [child for child in children if child not in excluded and not child.startswith(".")]
        sources.extend(Path(directory) / name for name in files if name.endswith(".py"))
    local_roots = {path.stem for path in root.glob("*.py")}
    local_roots.update(path.name for path in root.iterdir() if path.is_dir() and ((path / "__init__.py").is_file() or path.name == "scripts"))
    inventory = root / "release-allowlist.txt"
    if inventory.is_file():
        # A flattened ZIP can lose the WHOLE directory, including __init__.py.
        # Retain the expected roots independently of what survived extraction.
        for entry in inventory.read_text(encoding="utf-8-sig").splitlines():
            entry = entry.strip()
            if entry and not entry.startswith("#") and "/" in entry:
                top = entry.split("/")[0]
                # alembic/ is a migration workspace, not the installed Alembic
                # package. Its imports are validated by the dependency gate.
                if top.isidentifier() and top != "alembic":
                    local_roots.add(top)
    errors = []
    symbol_cache: dict[Path, tuple[set[str], bool]] = {}
    for source in sources:
        try:
            tree = ast.parse(source.read_text(encoding="utf-8-sig"), filename=str(source))
        except (SyntaxError, UnicodeError) as exc:
            errors.append(f"{source.relative_to(root)}: {exc}")
            continue
        package = list(source.relative_to(root).parts[:-1])
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    if node.level > len(package):
                        errors.append(f"{source.relative_to(root)}:{node.lineno}: relative import escapes package")
                        continue
                    parent = package[:len(package) - node.level + 1]
                    modules = [".".join(parent + (node.module.split(".") if node.module else []))]
                    if node.module is None:
                        modules = [f"{modules[0]}.{alias.name}" for alias in node.names if alias.name != "*"]
                else:
                    modules = [node.module or ""]
            else:
                continue
            for module in modules:
                if module.split(".")[0] not in local_roots:
                    continue
                path = root.joinpath(*module.split("."))
                namespace = path.is_dir() and any(path.glob("*.py"))
                if not path.with_suffix(".py").is_file() and not (path / "__init__.py").is_file() and not namespace:
                    errors.append(f"{source.relative_to(root)}:{node.lineno}: missing local module '{module}'")
                    continue
                if not isinstance(node, ast.ImportFrom) or (node.level and node.module is None):
                    continue
                module_file = path.with_suffix(".py") if path.with_suffix(".py").is_file() else path / "__init__.py"
                if module_file.is_file() and module_file not in symbol_cache:
                    names: set[str] = set()
                    dynamic = False
                    parsed = ast.parse(module_file.read_text(encoding="utf-8-sig"))
                    for declaration in _module_scope_nodes(parsed):
                        if isinstance(declaration, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                            names.add(declaration.name)
                            dynamic |= declaration.name == "__getattr__"
                        elif isinstance(declaration, ast.Name) and isinstance(declaration.ctx, ast.Store):
                            names.add(declaration.id)
                        elif isinstance(declaration, (ast.Import, ast.ImportFrom)):
                            for alias in declaration.names:
                                dynamic |= alias.name == "*"
                                names.add(alias.asname or alias.name.split(".")[0])
                    symbol_cache[module_file] = (names, dynamic)
                names, dynamic = symbol_cache.get(module_file, (set(), False))
                if dynamic:
                    continue
                for alias in node.names:
                    child = path / alias.name
                    if alias.name != "*" and alias.name not in names and not child.with_suffix(".py").is_file() and not (child / "__init__.py").is_file():
                        errors.append(f"{source.relative_to(root)}:{node.lineno}: missing symbol '{alias.name}' in '{module}'")
    return errors


if __name__ == "__main__":
    sys.exit(main())
