set -euo pipefail
python3 - <<'EOF'
import inspect
import app.main  # noqa: F401  registers all routes
from app.routes import ROUTES
handler = ROUTES[("POST", "/refunds")]
path = inspect.getsourcefile(handler).split("/app/", 1)[1]
print(f"POST /refunds is handled by {handler.__name__} in app/{path}. "
      "refunds.handle_refund registers the same route first, but approvals.py is imported later in "
      "app/main.py and its registration replaces it.")
EOF
