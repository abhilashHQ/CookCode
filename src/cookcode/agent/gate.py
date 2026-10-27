import fnmatch
import posixpath
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from cookcode.agent.tools import Tool, workspace_path
from cookcode.core.events import ApprovalResolved, Event

Mode = Literal["ask", "auto-edit", "yolo"]


class Rule(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool: str
    pattern: str

    @property
    def display(self) -> str:
        return f"{self.tool}:{self.pattern}"

    def matches(self, text: str) -> bool:
        return fnmatch.fnmatchcase(text, self.pattern)


class Policy(BaseModel):
    model_config = ConfigDict(frozen=True)

    allow: tuple[Rule, ...] = ()
    deny: tuple[Rule, ...] = ()


class GateDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: Literal["allow", "deny", "ask"]
    rule: str | None
    reason: str


def parse_rule(text: str | None) -> Rule | None:
    tool, sep, pattern = (text or "").partition(":")
    return Rule(tool=tool, pattern=pattern) if sep and tool and pattern else None


def _deny_forms(canonical: str, root: str) -> tuple[str, ...]:
    if posixpath.isabs(canonical):
        return (canonical,)
    return (canonical, posixpath.normpath(posixpath.join(root, canonical)))


def inside_workspace(path: str, cwd: str, root: str) -> bool:
    return not posixpath.isabs(workspace_path(root, cwd, path))


def subject(tool_name: str, arguments: dict[str, Any], cwd: str, root: str) -> str:
    if tool_name == "bash":
        command = arguments.get("command")
        return command if isinstance(command, str) else "*"
    path = arguments.get("path")
    return workspace_path(root, cwd, path) if isinstance(path, str) else "*"


class _Unbalanced(Exception):
    pass


@dataclass
class _Word:
    raw: str
    value: str
    dynamic: bool


@dataclass
class _Command:
    raw: str
    words: list[_Word]
    unsafe_redirect: bool


@dataclass
class _Pending:
    start: int | None = None
    end: int = 0
    words: list[_Word] = field(default_factory=list[_Word])
    unsafe: bool = False
    last_word_end: int = -1


_BLANKS = frozenset(" \t")
_OPERATORS = frozenset(";&|<>()\n")
_PARAM = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]|[@*#?$!-]")
_SIMPLE_BRACE = re.compile(r"\{#?(?:[A-Za-z_][A-Za-z0-9_]*|[0-9]+|[@*#?$!-])\}")
_FD = re.compile(r"[0-9]+|-")
_SAFE_TARGETS = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr"})
_NETWORK_TARGETS = ("/dev/tcp/", "/dev/udp/")
_MAX_NESTING = 32


class _Lexer:
    def __init__(self, text: str, level: int = 0) -> None:
        self.text = text
        self.i = 0
        self.level = level
        self.unanalyzable = "\x00" in text
        self.nested: list[_Command] = []

    def _peek(self, offset: int = 1) -> str:
        j = self.i + offset
        return self.text[j] if j < len(self.text) else ""

    def _flush(self, pending: _Pending, out: list[_Command]) -> _Pending:
        if pending.start is not None:
            raw = self.text[pending.start : pending.end].strip()
            out.append(_Command(raw, pending.words, pending.unsafe))
        return _Pending()

    def commands(self, closing: bool = False) -> list[_Command]:
        text = self.text
        out: list[_Command] = []
        pending = _Pending()
        depth = 0
        while self.i < len(text):
            c = text[self.i]
            nxt = self._peek()
            if c in _BLANKS:
                self.i += 1
            elif c == "#":
                newline = text.find("\n", self.i)
                self.i = len(text) if newline == -1 else newline
            elif c in "\n;":
                pending = self._flush(pending, out)
                self.i += 1
            elif c == "|":
                pending = self._flush(pending, out)
                self.i += 2 if nxt in ("|", "&") else 1
            elif c == "(":
                if nxt == "(":
                    self.unanalyzable = True
                pending = self._flush(pending, out)
                depth += 1
                self.i += 1
            elif c == ")":
                pending = self._flush(pending, out)
                if closing and depth == 0:
                    return out
                depth = max(depth - 1, 0)
                self.i += 1
            elif (c in "<>" and nxt != "(") or (c == "&" and nxt == ">"):
                op_start = self.i
                words = pending.words
                if words and pending.last_word_end == op_start and words[-1].raw.isdigit():
                    words.pop()
                if pending.start is None:
                    pending.start = op_start
                pending.unsafe = self._redirect() or pending.unsafe
                pending.end = self.i
            elif c == "&":
                pending = self._flush(pending, out)
                self.i += 2 if nxt == "&" else 1
            else:
                word_start = self.i
                pending.words.append(self._word())
                if pending.start is None:
                    pending.start = word_start
                pending.end = pending.last_word_end = self.i
        if closing:
            raise _Unbalanced
        self._flush(pending, out)
        return out

    def _redirect(self) -> bool:
        text = self.text
        c, nxt = text[self.i], self._peek()
        kind = "write"
        if c == "&":
            self.i += 3 if self._peek(2) == ">" else 2
        elif c == "<" and nxt == "<":
            self.unanalyzable = True
            self.i += 3 if self._peek(2) in ("<", "-") else 2
            kind = "read"
        elif c == "<":
            kind = {">": "write", "&": "dup"}.get(nxt, "read")
            self.i += 2 if nxt in (">", "&") else 1
        else:
            kind = "dup" if nxt == "&" else "write"
            self.i += 2 if nxt in (">", "|", "&") else 1
        while self.i < len(text) and text[self.i] in _BLANKS:
            self.i += 1
        at_operator = self.i < len(text) and text[self.i] in _OPERATORS
        if self.i >= len(text) or (
            at_operator and not (text[self.i] in "<>" and self._peek() == "(")
        ):
            self.unanalyzable = True
            return False
        target = self._word()
        if target.value.startswith(_NETWORK_TARGETS):
            return True
        if kind == "read" or (kind == "dup" and not target.dynamic and _FD.fullmatch(target.value)):
            return False
        return target.dynamic or target.value not in _SAFE_TARGETS

    def _word(self) -> _Word:
        text = self.text
        start = self.i
        value: list[str] = []
        dynamic = False
        brace = False
        while self.i < len(text):
            c = text[self.i]
            nxt = self._peek()
            if c in "<>" and nxt == "(":
                self.unanalyzable = True
                dynamic = True
                opened = self.i
                self._substitution(self.i + 2)
                value.append(text[opened : self.i])
            elif c in _BLANKS or c in _OPERATORS:
                break
            elif c == "\\":
                if nxt != "\n":
                    value.append(nxt or "\\")
                self.i = min(self.i + 2, len(text))
            elif c == "'":
                close = text.find("'", self.i + 1)
                if close == -1:
                    raise _Unbalanced
                value.append(text[self.i + 1 : close])
                self.i = close + 1
            elif c == '"':
                dynamic = self._double(value) or dynamic
            elif c == "`":
                self._backtick(value)
                dynamic = True
            elif c == "$" and nxt == "'":
                dynamic = self._ansi(value) or dynamic
            elif c == "$" and nxt == '"':
                self.i += 1
            elif c == "$":
                dynamic = self._dollar(value) or dynamic
            else:
                dynamic = dynamic or c in "*?["
                brace = brace or c == "{"
                value.append(c)
                self.i += 1
        raw = text[start : self.i]
        return _Word(raw, "".join(value), dynamic or (brace and raw != "{"))

    def _double(self, value: list[str]) -> bool:
        text = self.text
        dynamic = False
        self.i += 1
        while self.i < len(text):
            c = text[self.i]
            nxt = self._peek()
            if c == '"':
                self.i += 1
                return dynamic
            if c == "\\" and nxt in ("$", "`", '"', "\\", "\n"):
                if nxt != "\n":
                    value.append(nxt)
                self.i += 2
            elif c == "`":
                self._backtick(value)
                dynamic = True
            elif c == "$":
                dynamic = self._dollar(value) or dynamic
            else:
                value.append(c)
                self.i += 1
        raise _Unbalanced

    def _ansi(self, value: list[str]) -> bool:
        text = self.text
        escaped = False
        self.i += 2
        while self.i < len(text):
            c = text[self.i]
            if c == "'":
                self.i += 1
                return escaped
            if c == "\\":
                value.append(text[self.i : self.i + 2])
                escaped = True
                self.i += 2
            else:
                value.append(c)
                self.i += 1
        raise _Unbalanced

    def _dollar(self, value: list[str]) -> bool:
        text = self.text
        start = self.i
        nxt = self._peek()
        if nxt == "(":
            self.unanalyzable = True
            self._substitution(self.i + 2)
        elif nxt == "{" and (simple := _SIMPLE_BRACE.match(text, self.i + 1)):
            self.i = simple.end()
        elif nxt in ("{", "["):
            self.unanalyzable = True
            self.i += 1
            self._skip_group(nxt, "}" if nxt == "{" else "]")
        elif param := _PARAM.match(text, self.i + 1):
            self.i = param.end()
        else:
            value.append("$")
            self.i += 1
            return False
        value.append(text[start : self.i])
        return True

    def _enter(self) -> None:
        self.level += 1
        if self.level > _MAX_NESTING:
            raise _Unbalanced

    def _substitution(self, pos: int) -> None:
        self._enter()
        self.i = pos
        self.nested.extend(self.commands(closing=True))
        self.level -= 1
        self.i += 1

    def _skip_group(self, opening: str, closing: str) -> None:
        self._enter()
        text = self.text
        depth = 0
        while self.i < len(text):
            c = text[self.i]
            if c == "\\":
                self.i += 2
                continue
            if c == "'":
                close = text.find("'", self.i + 1)
                if close == -1:
                    raise _Unbalanced
                self.i = close + 1
                continue
            if c == '"':
                self._double([])
                continue
            if c == "`":
                self._backtick([])
                continue
            if c == "$" and self._peek() in ("(", "{", "["):
                self._dollar([])
                continue
            if c == opening:
                depth += 1
            elif c == closing:
                depth -= 1
                if depth == 0:
                    self.level -= 1
                    self.i += 1
                    return
            self.i += 1
        raise _Unbalanced

    def _backtick(self, value: list[str]) -> None:
        text = self.text
        self.unanalyzable = True
        inner: list[str] = []
        j = self.i + 1
        while j < len(text) and text[j] != "`":
            if text[j] == "\\" and j + 1 < len(text):
                nxt = text[j + 1]
                inner.append(nxt if nxt in "$`\\" else "\\" + nxt)
                j += 2
            else:
                inner.append(text[j])
                j += 1
        if j >= len(text):
            raise _Unbalanced
        value.append(text[self.i : j + 1])
        self.i = j + 1
        if self.level >= _MAX_NESTING:
            raise _Unbalanced
        sub = _Lexer("".join(inner), self.level + 1)
        self.nested.extend(sub.commands())
        self.nested.extend(sub.nested)


def split_command(command: str) -> list[str] | None:
    lexer = _Lexer(command)
    try:
        commands = lexer.commands()
    except _Unbalanced:
        return None
    if lexer.unanalyzable:
        return None
    return [c.raw for c in commands]


def _frozen(names: str) -> frozenset[str]:
    return frozenset(names.split())


@dataclass(frozen=True)
class _Wrapper:
    flags: frozenset[str] = frozenset()
    with_arg: frozenset[str] = frozenset()
    positional: int = 0
    assignments: bool = False
    opaque: bool = False
    needs_command: bool = False


_WRAPPERS = {
    "command": _Wrapper(flags=_frozen("-p -v -V")),
    "builtin": _Wrapper(),
    "exec": _Wrapper(flags=_frozen("-c -l -cl -lc"), with_arg=_frozen("-a")),
    "nohup": _Wrapper(),
    "time": _Wrapper(
        flags=_frozen("-p -v -a -q --verbose --append --quiet --portability"),
        with_arg=_frozen("-f -o --format --output"),
    ),
    "nice": _Wrapper(with_arg=_frozen("-n --adjustment")),
    "env": _Wrapper(
        flags=_frozen("- -i -0 -v --ignore-environment --null --debug"),
        with_arg=_frozen("-u -C --unset --chdir"),
        assignments=True,
    ),
    "sudo": _Wrapper(
        flags=_frozen(
            "-A -b -E -H -k -K -n -P -S -i -s --askpass --background --preserve-env --set-home "
            "--login --non-interactive --shell --stdin --reset-timestamp --remove-timestamp"
        ),
        with_arg=_frozen(
            "-u -g -h -p -C -D -r -t -U -T -R --user --group --host --prompt --close-from "
            "--chdir --role --type --other-user --command-timeout --chroot"
        ),
        needs_command=True,
    ),
    "doas": _Wrapper(flags=_frozen("-n -s"), with_arg=_frozen("-u -C"), needs_command=True),
    "timeout": _Wrapper(
        flags=_frozen("-v --verbose --preserve-status --foreground"),
        with_arg=_frozen("-s -k --signal --kill-after"),
        positional=1,
    ),
    "stdbuf": _Wrapper(with_arg=_frozen("-i -o -e --input --output --error")),
    "setsid": _Wrapper(flags=_frozen("-c -f -w --ctty --fork --wait")),
    "xargs": _Wrapper(
        flags=_frozen(
            "-0 -o -p -r -t -x --null --open-tty --interactive --no-run-if-empty --verbose --exit"
        ),
        with_arg=_frozen(
            "-a -d -E -I -L -n -P -s --arg-file --delimiter --max-args --max-procs "
            "--max-chars --max-lines --process-slot-var"
        ),
        opaque=True,
    ),
    "busybox": _Wrapper(),
}
_ASSIGNMENT = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(?:\[[^\]]*\])?\+?=")
_PREFIX_WORDS = _frozen("! { } if then else elif fi do done while until coproc")
_SHELLS = _frozen("sh bash rbash zsh dash ksh mksh ash yash fish csh tcsh")
_OPAQUE = _frozen(
    "source . trap alias let [[ declare typeset local mapfile readarray enable hash read unset"
)
_DANGEROUS_VARS = _frozen("BASH_ENV ENV PS4 PROMPT_COMMAND SHELLOPTS BASHOPTS PATH IFS")
_DANGEROUS_PREFIXES = ("LD_", "DYLD_", "BASH_FUNC_")
_EXEC_FLAGS = _frozen("-exec -execdir -ok -okdir")
_MAX_DEPTH = 8
_FORM_BUDGET = 512


@dataclass
class _Budget:
    left: int = _FORM_BUDGET


def _suspicious(word: _Word) -> bool:
    if "$(" in word.value or "`" in word.value:
        return True
    assignment = _ASSIGNMENT.match(word.value)
    if assignment is None:
        return False
    name = assignment.group(1)
    subscripted = word.value[len(name)] == "["
    return subscripted or name in _DANGEROUS_VARS or name.startswith(_DANGEROUS_PREFIXES)


def _wrapped_start(spec: _Wrapper, words: list[_Word]) -> tuple[list[int], bool]:
    i = 1
    while i < len(words):
        value = words[i].value
        if spec.assignments and _ASSIGNMENT.match(value):
            i += 1
        elif value == "--":
            i += 1
            break
        elif value in spec.flags or (
            value.startswith("--") and value.partition("=")[0] in spec.flags | spec.with_arg
        ):
            i += 1
        elif value in spec.with_arg:
            i += 2
        elif not value.startswith("--") and value[:2] in spec.with_arg:
            i += 1
        elif value.startswith("-") and len(value) > 1:
            return list(range(i + 1, len(words))), True
        else:
            break
    i += spec.positional
    if i >= len(words):
        return [], spec.opaque or spec.needs_command
    return [i], spec.opaque


def _shell_script(words: list[_Word]) -> _Word | None:
    i = 1
    command_mode = False
    while i < len(words):
        value = words[i].value
        if value in ("-", "--"):
            i += 1
            break
        if value.startswith("--"):
            i += 2 if value in ("--rcfile", "--init-file") else 1
        elif len(value) > 1 and value[0] in "-+":
            command_mode = command_mode or (value[0] == "-" and "c" in value[1:])
            i += 2 if value[-1] in "oO" else 1
        else:
            break
    return words[i] if command_mode and i < len(words) else None


def _su_script(words: list[_Word]) -> _Word | None:
    for i, word in enumerate(words[1:], start=1):
        value = word.value
        for prefix in ("--command=", "--session-command="):
            if value.startswith(prefix):
                return _Word(word.raw, value[len(prefix) :], word.dynamic)
        clustered = value.startswith("-") and not value.startswith("--") and "c" in value[1:]
        if value in ("--command", "--session-command") or (clustered and value.endswith("c")):
            return words[i + 1] if i + 1 < len(words) else None
        if clustered:
            return _Word(word.raw, value[value.index("c", 1) + 1 :], word.dynamic)
    return None


def _find_execs(words: list[_Word]) -> list[list[_Word]]:
    found: list[list[_Word]] = []
    i = 1
    while i < len(words):
        if words[i].value in _EXEC_FLAGS:
            j = i + 1
            while j < len(words) and words[j].value not in (";", "+"):
                j += 1
            found.append(words[i + 1 : j])
            i = j
        i += 1
    return found


def _command_start(words: list[_Word]) -> int:
    i = 0
    while i < len(words) and (words[i].value in _PREFIX_WORDS or _ASSIGNMENT.match(words[i].value)):
        i += 1
    return i


def _forms(words: list[_Word], depth: int, budget: _Budget) -> tuple[list[str], bool]:
    budget.left -= 1
    if depth > _MAX_DEPTH or budget.left < 0:
        return [], True
    opaque = any(_suspicious(word) for word in words)
    i = _command_start(words)
    if i == len(words):
        return [], opaque
    rest = words[i:]
    head = rest[0]
    args = [word.value for word in rest[1:]]
    texts = [" ".join([head.value, *args])]
    if head.dynamic:
        return texts, True
    name = posixpath.basename(head.value) or head.value
    if name != head.value:
        texts.append(" ".join([name, *args]))
    nested: list[tuple[list[str], bool]] = []
    printf_v = name == "printf" and any(a.startswith("-v") for a in args)
    test_v = name in ("test", "[") and "-v" in args
    if name in _OPAQUE or printf_v or test_v:
        opaque = True
    elif name == "eval":
        nested.append(_text_forms(" ".join(args), depth + 1, budget))
        opaque = opaque or any(word.dynamic for word in rest[1:])
    elif name in _SHELLS or name == "su":
        script = _shell_script(rest) if name in _SHELLS else _su_script(rest)
        if script is None:
            opaque = True
        else:
            nested.append(_text_forms(script.value, depth + 1, budget))
            opaque = opaque or script.dynamic
    elif name == "find":
        nested.extend(_forms(inner, depth + 1, budget) for inner in _find_execs(rest))
    elif name in _WRAPPERS:
        starts, wrapped_opaque = _wrapped_start(_WRAPPERS[name], rest)
        opaque = opaque or wrapped_opaque
        nested.extend(_forms(rest[start:], depth + 1, budget) for start in starts)
    for more, more_opaque in nested:
        texts.extend(more)
        opaque = opaque or more_opaque
    return texts, opaque


@dataclass
class _Analysis:
    commands: list[_Command] | None
    texts: list[str]
    opaque: bool


def _fragments(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"[;&|\n()`]+", text) if part.strip()]


def _analyze(text: str, depth: int, budget: _Budget) -> _Analysis:
    lexer = _Lexer(text)
    try:
        commands = lexer.commands()
    except _Unbalanced:
        return _Analysis(None, [text.strip(), *_fragments(text)], True)
    texts = [text.strip()]
    opaque = lexer.unanalyzable
    for command in [*commands, *lexer.nested]:
        texts.append(command.raw)
        more, more_opaque = _forms(command.words, depth, budget)
        texts.extend(more)
        opaque = opaque or more_opaque
    return _Analysis(None if lexer.unanalyzable else commands, texts, opaque)


def _text_forms(text: str, depth: int, budget: _Budget) -> tuple[list[str], bool]:
    if depth > _MAX_DEPTH:
        return [text], True
    analysis = _analyze(text, depth, budget)
    return analysis.texts, analysis.opaque


def _deny(rule: Rule) -> GateDecision:
    return GateDecision(
        action="deny", rule=rule.display, reason=f"matches deny rule {rule.display}"
    )


def _allow_by_rules(matched: list[Rule]) -> GateDecision:
    names = ", ".join(dict.fromkeys(rule.display for rule in matched))
    return GateDecision(action="allow", rule=matched[0].display, reason=f"allowed by {names}")


def _by_mode(mode: Mode, action: Literal["allow", "ask"], reason: str) -> GateDecision:
    return GateDecision(action=action, rule=f"mode:{mode}", reason=reason)


def _bash_allow(analysis: _Analysis, allow: list[Rule], has_deny: bool) -> list[Rule] | str:
    if analysis.commands is None:
        return "the command uses substitution, a here-document or unbalanced quoting"
    if not analysis.commands:
        return "the command is empty"
    if analysis.opaque and has_deny:
        return "the command runs code the gate cannot inspect, so deny rules cannot be verified"
    matched: list[Rule] = []
    for command in analysis.commands:
        if command.unsafe_redirect:
            return f"subcommand {command.raw!r} redirects output to a file"
        start = _command_start(command.words)
        if any(_suspicious(word) for word in command.words[:start]):
            return f"subcommand {command.raw!r} sets a variable that changes how commands run"
        executed = " ".join(word.value for word in command.words[start:])
        rule = next((r for r in allow if r.matches(executed)), None)
        if rule is None:
            return f"subcommand {command.raw!r} matches no allow rule"
        matched.append(rule)
    return matched


def _decide_bash(
    arguments: dict[str, Any], mode: Mode, deny: list[Rule], allow: list[Rule]
) -> GateDecision:
    command = arguments.get("command")
    if isinstance(command, str):
        analysis = _analyze(command, 0, _Budget())
    else:
        analysis = _Analysis(None, [], True)
    for rule in deny:
        if any(rule.matches(text) for text in analysis.texts):
            return _deny(rule)
    if mode == "yolo":
        return _by_mode(mode, "allow", "yolo mode allows every call no deny rule matches")
    outcome = _bash_allow(analysis, allow, bool(deny))
    if isinstance(outcome, list):
        return _allow_by_rules(outcome)
    return _by_mode(mode, "ask", f"bash needs approval: {outcome}")


def _validate(tool: Tool[Any], arguments: dict[str, Any]) -> BaseModel | None:
    try:
        return tool.args_model.model_validate(arguments)
    except ValidationError:
        return None


def _decide_tool(
    tool: Tool[Any],
    arguments: dict[str, Any],
    cwd: str,
    root: str,
    mode: Mode,
    deny: list[Rule],
    allow: list[Rule],
) -> GateDecision:
    validated = _validate(tool, arguments)
    touched = tool.touches(validated) if tool.touches and validated is not None else None
    if touched:
        subjects = [workspace_path(root, cwd, path) for path in touched]
    else:
        dumped = validated.model_dump() if validated is not None else arguments
        subjects = [subject(tool.name, dumped, cwd, root)]
    deny_forms = [form for s in subjects for form in _deny_forms(s, root)]
    for rule in deny:
        if any(rule.matches(form) for form in deny_forms):
            return _deny(rule)
    if mode == "yolo":
        return _by_mode(mode, "allow", "yolo mode allows every call no deny rule matches")
    matched = [next((r for r in allow if r.matches(s)), None) for s in subjects]
    rules = [rule for rule in matched if rule is not None]
    if subjects and len(rules) == len(subjects):
        return _allow_by_rules(rules)
    if tool.side_effect == "read_only":
        return _by_mode(mode, "allow", "read-only calls are allowed")
    if (
        mode == "auto-edit"
        and tool.side_effect == "mutating"
        and touched
        and all(inside_workspace(path, cwd, root) for path in touched)
    ):
        return _by_mode(mode, "allow", "auto-edit allows file edits inside the workspace")
    return _by_mode(mode, "ask", f"{tool.side_effect} call needs approval in {mode} mode")


def decide(
    *,
    tool: Tool[Any],
    arguments: dict[str, Any],
    cwd: str,
    root: str,
    mode: Mode,
    policy: Policy,
    session_allow: Sequence[Rule],
) -> GateDecision:
    deny = [rule for rule in policy.deny if rule.tool == tool.name]
    allow = [rule for rule in (*policy.allow, *session_allow) if rule.tool == tool.name]
    if tool.name == "bash":
        return _decide_bash(arguments, mode, deny, allow)
    return _decide_tool(tool, arguments, cwd, root, mode, deny, allow)


def covers(
    rule: Rule,
    *,
    tool: Tool[Any],
    arguments: dict[str, Any],
    cwd: str,
    root: str,
    policy: Policy,
) -> bool:
    if rule.tool != tool.name:
        return False
    decision = decide(
        tool=tool,
        arguments=arguments,
        cwd=cwd,
        root=root,
        mode="ask",
        policy=Policy(deny=policy.deny),
        session_allow=(rule,),
    )
    return decision.action == "allow" and decision.rule == rule.display


def session_rules(events: Sequence[Event]) -> list[Rule]:
    rules: list[Rule] = []
    for event in events:
        body = event.body
        if not isinstance(body, ApprovalResolved) or body.decision != "allow_always":
            continue
        rule = parse_rule(body.rule)
        if rule is not None:
            rules.append(rule)
    return rules
