import ast
import pathlib
from collections import defaultdict

files = sorted(pathlib.Path("C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component").rglob("*.py")) + [
    pathlib.Path("C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/app.py"),
    pathlib.Path("C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/tests/test_golden_master.py"),
]
defs, imports, calls = {}, {}, defaultdict(set)


for f in files:
    if not f.exists():
        continue
    tree = ast.parse(f.read_text(encoding="utf-8"))
    rel = f.as_posix()
    imports[rel] = [
        ast.unparse(n)
        for n in tree.body
        if isinstance(n, (ast.Import, ast.ImportFrom))
    ]
    defs[rel] = []
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = (
                ast.get_docstring(n).splitlines()[0]
                if ast.get_docstring(n)
                else ""
            )
            sig = ast.unparse(n).splitlines()[0]
            doc = ast.get_docstring(n) or ""
            loc = getattr(n, "end_lineno", n.lineno) - n.lineno + 1
            defs[rel].append((n.name, sig, doc, loc))
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            calls[n.id].add(rel)

with open("repo_map.md", "w", encoding="utf-8") as out:
    for rel, dlist in defs.items():
        out.write(f"## {rel}\n**Imports:** " + "; ".join(imports[rel]) + "\n")
        for name, sig, doc, loc in dlist:
            used_in = sorted(calls[name] - {rel})
            doc_str = f" — {doc}" if doc else ""
            caller_str = ", ".join(used_in) or "local"
            out.write(
                f"- `{sig}` ({loc}L){doc_str} | **Callers:** {caller_str}\n"
            )
        out.write("\n")

print("Wrote repo_map.md")