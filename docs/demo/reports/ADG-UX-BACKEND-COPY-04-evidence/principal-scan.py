"""List non-docstring string constants (incl. f-strings) containing the word 'principal'."""
import ast, pathlib, re, sys
root = pathlib.Path(sys.argv[1])
WORD = re.compile(r"\b[Pp]rincipal(s|'s)?\b")
TECH = re.compile(r"foreign security principal|user principal name|well-known SYSTEM principal", re.I)
for path in sorted(root.rglob("*.py")):
    if path.name.startswith("._"):
        continue
    tree = ast.parse(path.read_text())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docs.add(id(body[0].value))
        # attribute docstrings: bare string expr statements
        if isinstance(node, ast.Expr) and isinstance(node.value, (ast.Constant, ast.JoinedStr)):
            docs.add(id(node.value))
    inner = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for v in node.values:
                inner.add(id(v))
    for node in ast.walk(tree):
        if id(node) in docs or id(node) in inner:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, ast.JoinedStr):
            text = "".join(v.value if isinstance(v, ast.Constant) else "{…}" for v in node.values)
        else:
            continue
        if " " not in text.strip() or not WORD.search(text):
            continue
        tag = "TECH " if TECH.search(text) and not WORD.search(TECH.sub("", text)) else ""
        print(f"{path.relative_to(root.parent)}:{node.lineno}: {tag}{text!r}")
