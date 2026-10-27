set -euo pipefail
python3 - <<'EOF'
from pathlib import Path

p = Path("tokens.py")
s = p.read_text()
s = s.replace("from datetime import datetime, timedelta\n", "from datetime import datetime, timedelta, timezone\n")
s = s.replace("datetime.utcnow()", "datetime.now(timezone.utc)")
s = s.replace('datetime.utcfromtimestamp(claims["exp"])', 'datetime.fromtimestamp(claims["exp"], timezone.utc)')
s = s.replace('datetime.utcfromtimestamp(_claims(token)["exp"])', 'datetime.fromtimestamp(_claims(token)["exp"], timezone.utc)')
p.write_text(s)

p = Path("audit.py")
s = p.read_text()
s = s.replace("from datetime import datetime\n", "from datetime import datetime, timezone\n")
s = s.replace("datetime.utcnow()", "datetime.now(timezone.utc)")
s = s.replace("    return datetime.strptime(text, STAMP_FORMAT)\n",
              "    return datetime.strptime(text, STAMP_FORMAT).replace(tzinfo=timezone.utc)\n")
p.write_text(s)

p = Path("test_tokens.py")
s = p.read_text()
s = s.replace("from datetime import datetime, timedelta\n", "from datetime import datetime, timedelta, timezone\n")
s = s.replace("datetime.utcnow()", "datetime.now(timezone.utc)")
s = s.replace("datetime(2024, 1, 2, 3, 4, 5)", "datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)")
s = s.replace("datetime(2024, 1, 1)", "datetime(2024, 1, 1, tzinfo=timezone.utc)")
p.write_text(s)
EOF
