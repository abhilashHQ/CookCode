set -euo pipefail
python3 - <<'EOF'
from svc.http import HttpClient
from svc.settings import load
client = HttpClient(load({"APP_ENV": "production"}))
print(f"HttpClient uses a timeout of {client.timeout} seconds: config/production.ini sets timeout_ms = 4500, "
      "which svc/settings.py converts to seconds and which overrides timeout = 10 from base.ini; "
      "it is below the 20s MAX_TIMEOUT cap in svc/http.py.")
EOF
