set -euo pipefail
python3 - <<'EOF'
from pathlib import Path
p = Path("models.py")
s = p.read_text()
s = s.replace('def __init__(self, title, body="", tags=[], created=None):',
              'def __init__(self, title, body="", tags=None, created=None):')
s = s.replace("self.tags = tags\n", "self.tags = list(tags) if tags is not None else []\n")
p.write_text(s)
EOF
