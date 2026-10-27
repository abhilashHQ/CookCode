set -euo pipefail
cat > commands/count.py <<'EOF'
def run(args):
    from registry import COMMANDS

    return str(len(COMMANDS))
EOF
sed -i 's/^from commands import hello, help_cmd$/from commands import count, hello, help_cmd/; s/^    "help": help_cmd.run,$/    "help": help_cmd.run,\n    "count": count.run,/' registry.py
echo '"count" = "Count commands"' >> pyproject.toml
