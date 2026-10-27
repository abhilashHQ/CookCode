set -euo pipefail
python3 - <<'EOF'
import json; from pathlib import Path
p = Path("locale/en.json"); m = json.loads(p.read_text()); m["count"] = "You have {n} items."
p.write_text(json.dumps(m, indent=2) + "\n")
c = Path("cli.py"); s = c.read_text()
s = s.replace("    return 2\n", '    if argv[:1] == ["count"]:\n        print(t("count", n=argv[1]))\n        return 0\n    return 2\n')
c.write_text(s)
EOF
