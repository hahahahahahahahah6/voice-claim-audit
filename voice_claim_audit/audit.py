"""Inspect Markdown and Python syntax without executing project code."""
import argparse
import ast
import fnmatch
import json
import re
from pathlib import Path

VOICE = ("voice recognition", "speech recognition", "speech to text", "text to speech", "voice commands", "voice command", "voice control", "voice input", "voice output", "voice responses", "voice response", "speaks responses", "speak responses", "voice assistant", "voice interface", "voice interaction", "voice activated", "voice driven", "voice enabled", "voice support", "voice powered", "voice based", "supports voice", "supports speech", "uses voice", "using voice", "via voice", "with voice", "over voice")
NOVOICE = ("no voice input", "no voice output", "no voice support", "no voice recognition", "no speech recognition", "does not support voice", "does not use voice", "does not speak", "voice not supported", "speech not supported", "without voice", "no voice", "no speech")
ROUTES = {"speech_recognition": ("Recognizer", None), "whisper": ("load_model", {"transcribe"}), "pyttsx3": ("init", {"say"}), "gtts": ("gTTS", {"save", "write_to_fp"})}

def normalize(text):
    return re.sub(r"\s+", " ", text.lower().replace("n't", " not").replace("-", " ")).strip()

def pattern(phrases):
    return re.compile(r"\b(?:" + "|".join(re.escape(p) for p in sorted(phrases, key=len, reverse=True)) + r")\b")

def claims(text):
    lines = []
    fence = None
    for line in text.splitlines():
        match = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if match:
            token = match.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not line[match.end():].strip():
                fence = None
            lines.append("")
        elif fence is None:
            lines.append(line)
    positive = negative = False
    for paragraph in re.split(r"\n\s*\n", "\n".join(lines)):
        text = normalize(paragraph)
        negative |= bool(pattern(NOVOICE).search(text))
        text = pattern(NOVOICE).sub(" ", text)
        positive |= bool(pattern(VOICE).search(text))
    return positive, negative

def dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted(node.value)
        return base + "." + node.attr if base else None
    return None

def evidence(source):
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    modules, names = {}, {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                if item.name.split(".")[0] in ROUTES:
                    modules[item.asname or item.name.split(".")[0]] = item.name if item.asname else item.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            provider = node.module.split(".")[0]
            if provider in ROUTES:
                for item in node.names:
                    if item.name == ROUTES[provider][0]:
                        names[item.asname or item.name] = provider
    def creation(node, provider):
        if not isinstance(node, ast.Call):
            return False
        func = node.func
        if isinstance(func, ast.Name):
            return names.get(func.id) == provider
        path = dotted(func)
        if not path or not isinstance(func, ast.Attribute) or func.attr != ROUTES[provider][0]:
            return False
        bits = path.split(".")
        resolved = modules.get(bits[0])
        return bool(resolved and resolved.split(".")[0] == provider)
    found = []
    nodes = list(ast.walk(tree))
    for provider, (_, methods) in ROUTES.items():
        bindings = set()
        for node in nodes:
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and creation(node.value, provider):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                bindings.update(filter(None, (dotted(t) for t in targets)))
        for node in nodes:
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                attr, receiver = node.func.attr, node.func.value
                valid = attr.startswith("recognize_") if methods is None else attr in methods
                if valid and (creation(receiver, provider) or (dotted(receiver) is not None and dotted(receiver) in bindings)):
                    found.append(provider)
                    break
    return found

def audit(root, ignores=(), fail_on="HIGH"):
    root = Path(root)
    if not root.is_dir():
        raise ValueError("root must be a readable directory")
    if not root.stat().st_mode & 0o444 or not root.stat().st_mode & 0o111:
        raise ValueError("root must be a readable directory")
    positive = negative = False
    prod, tests, errors = [], [], []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if path.is_symlink() or any((root.joinpath(*rel.parts[:i])).is_symlink() for i in range(1, len(rel.parts))) or not path.is_file():
            continue
        name = rel.as_posix()
        if any(fnmatch.fnmatchcase(name, glob) for glob in ignores):
            continue
        doc = name == "README.md" or ("docs" in rel.parts[:-1] and path.suffix.lower() == ".md")
        if doc:
            p, n = claims(path.read_text(encoding="utf-8"))
            positive |= p
            negative |= n
        if path.suffix != ".py":
            continue
        if any(part in {"fixtures", "docs", "examples"} for part in rel.parts[:-1]):
            continue
        test = "tests" in rel.parts[:-1] or path.name.startswith("test_") or path.name.endswith("_test.py")
        source = path.read_text(encoding="utf-8")
        try:
            ast.parse(source)
        except (SyntaxError, ValueError) as exc:
            errors.append({"path": name, "message": str(exc)})
        routes = evidence(source)
        if routes:
            (tests if test else prod).append({"path": name, "providers": routes})
    findings = []
    if positive and not prod:
        findings.append({"rule": "documented-voice-claim-false", "severity": "MEDIUM" if tests else "HIGH", "message": "Documented spoken capability lacks production source evidence."})
    if negative and prod:
        findings.append({"rule": "documented-no-voice-claim-false", "severity": "HIGH", "message": "Documented absence contradicts production source evidence."})
    failed = any(f["severity"] == "HIGH" or fail_on == "MEDIUM" for f in findings)
    return {"verdict": "FAIL" if failed else "PASS", "findings": findings, "claims": {"positive": positive, "negative": negative}, "evidence": prod, "test_evidence": tests, "parse_errors": errors}

class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(1, self.format_usage() + "error: " + message + "\n")

def main(argv=None):
    parser = Parser(description="Audit documented spoken capabilities against static source evidence.")
    parser.add_argument("root")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--ignore", action="append", default=[])
    parser.add_argument("--fail-on", choices=["HIGH", "MEDIUM"], default="HIGH")
    parser.add_argument("--version", action="version", version="0.1.0")
    args = parser.parse_args(argv)
    try:
        result = audit(args.root, args.ignore, args.fail_on)
    except (OSError, UnicodeError, ValueError) as exc:
        parser.exit(1, "error: " + str(exc) + "\n")
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(result["verdict"])
        for finding in result["findings"]:
            print("{severity} {rule}: {message}".format(**finding))
        if args.verbose:
            for key in ("evidence", "test_evidence", "parse_errors"):
                print(key + ": " + json.dumps(result[key], sort_keys=True))
    return 2 if result["verdict"] == "FAIL" else 0
