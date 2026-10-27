set -euo pipefail
sed -i 's/                except BaseException as exc:/                except retry_on as exc:/' apiclient/retry.py
grep -q "except retry_on as exc:" apiclient/retry.py
