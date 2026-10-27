set -euo pipefail
python3 - <<'EOF'
import json; from pathlib import Path
p = Path("locale/en.json"); m = json.loads(p.read_text()); m["farewell"] = "Goodbye, {name}!"
p.write_text(json.dumps(m, indent=2) + "\n")
c = Path("cli.py"); s = c.read_text()
s = s.replace('print(t("greeting", name=argv[1]))\n', 'print(t("greeting", name=argv[1]))\n        print(t("farewell", name=argv[1]))\n')
c.write_text(s)
t = Path("test_cli.py"); s = t.read_text()
t.write_text(s.replace('== "Hello, Ada!\\n"', '== "Hello, Ada!\\nGoodbye, Ada!\\n"'))
EOF
