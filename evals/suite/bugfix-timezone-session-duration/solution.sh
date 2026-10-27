set -euo pipefail
python3 - <<'EOF'
from pathlib import Path
p = Path("timetrack/parsing.py")
s = p.read_text()
s = s.replace("from datetime import datetime\n", "from datetime import datetime, timezone\n")
s = s.replace(
    "    return dt.replace(tzinfo=None)\n",
    "    if dt.tzinfo is None:\n"
    "        return dt.replace(tzinfo=timezone.utc)\n"
    "    return dt.astimezone(timezone.utc)\n",
)
p.write_text(s)
EOF
