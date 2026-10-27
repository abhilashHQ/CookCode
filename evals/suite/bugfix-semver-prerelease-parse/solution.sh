set -euo pipefail
python3 - <<'EOF'
from pathlib import Path
p = Path("versioning.py")
s = p.read_text()
ident = r"[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*"
s = s.replace(r'r"(?:-([0-9A-Za-z-]+))?"', 'r"(?:-(' + ident + '))?"')
s = s.replace(r'r"(?:\+([0-9A-Za-z-]+))?$"', r'r"(?:\+(' + ident + '))?$"')
p.write_text(s)
EOF
