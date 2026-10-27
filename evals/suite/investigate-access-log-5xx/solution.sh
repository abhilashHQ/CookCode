set -euo pipefail
awk '$9 ~ /^5[0-9][0-9]$/ {c[$1]++} END {for (ip in c) print c[ip], ip}' logs/access.log \
  | sort -rn | head -1 \
  | awk '{print "The IP with the most 5xx responses is " $2 ", with " $1 " 5xx responses."}'
