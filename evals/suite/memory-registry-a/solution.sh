set -euo pipefail
cat > commands/version.py <<'EOF'
import tomllib
from pathlib import Path


def run(args):
    data = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text())
    return data["project"]["version"]
EOF
sed -i 's/^from commands import hello, help_cmd$/from commands import hello, help_cmd, version/; s/^    "help": help_cmd.run,$/    "help": help_cmd.run,\n    "version": version.run,/' registry.py
echo '"version" = "Print the version"' >> pyproject.toml
