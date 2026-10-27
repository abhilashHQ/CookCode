set -euo pipefail
rpm=$(python3 -c 'from apiclient.config import DEFAULT_RPM; print(DEFAULT_RPM)')
echo "The current default is ${rpm} requests per minute (FREE_TIER_RPM * 2 in apiclient/config.py)."
echo "Release 1.6.0 first introduced it: 1.4.0 added a 60 rpm throttle, 1.6.0 gave paid plans double that (120 rpm), and 2.0.0 later made it the default for every client."
