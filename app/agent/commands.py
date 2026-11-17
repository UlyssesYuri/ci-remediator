import shlex

PREFIXES = (
    "pytest",
    "python -m pytest",
    "python3 -m pytest",
    "npm test",
    "npm run test",
    "pnpm test",
    "yarn test",
    "go test",
    "cargo test",
    "mvn test",
    "make test",
)

SETUP_PREFIXES = (
    "make build",
    "make install",
    "pip install",
    "python -m pip install",
    "python3 -m pip install",
    "npm ci",
    "npm install",
    "pnpm install",
    "yarn install",
    "go mod download",
)

_BLOCK_STARTS = ("", "|", ">", "|-", ">-")


def run_scripts(workflow_text: str) -> list[str]:
    scripts: list[str] = []
    lines = workflow_text.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        stripped = raw.strip()
        indent = len(raw) - len(raw.lstrip())
        candidate = stripped[2:] if stripped.startswith("- ") else stripped

        if candidate.startswith("run:"):
            value = candidate[len("run:"):].strip()
            if value in _BLOCK_STARTS:
                i += 1
                while i < n:
                    line = lines[i]
                    if not line.strip():
                        i += 1
                        continue
                    line_indent = len(line) - len(line.lstrip())
                    if line_indent <= indent:
                        break
                    scripts.append(line.strip())
                    i += 1
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            scripts.append(value)
        i += 1
    return scripts


def match_allowlist(command: str, prefixes: tuple[str, ...] = PREFIXES) -> list[str] | None:
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    for prefix in prefixes:
        tokens = prefix.split()
        if argv[: len(tokens)] == tokens:
            return argv
    return None


def named_by_claude(diagnosis: str) -> str | None:
    named = None
    for line in diagnosis.splitlines():
        stripped = line.strip()
        if stripped.startswith("VALIDATE:"):
            named = stripped[len("VALIDATE:"):].strip()
    return named


def plan_for(workflow_text: str, diagnosis: str) -> tuple[list[list[str]], list[str] | None]:
    setup: list[list[str]] = []
    for script in run_scripts(workflow_text):
        gate = match_allowlist(script, PREFIXES)
        if gate is not None:
            return setup, gate
        step = match_allowlist(script, SETUP_PREFIXES)
        if step is not None:
            setup.append(step)

    # Claude can never add a setup step, only name the gate.
    name = named_by_claude(diagnosis)
    gate = match_allowlist(name, PREFIXES) if name else None
    return setup, gate


def command_for(workflow_text: str, diagnosis: str) -> list[str] | None:
    return plan_for(workflow_text, diagnosis)[1]
