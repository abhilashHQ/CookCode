set -euo pipefail
sed -i 's/self.counts\[item\] = have$/self.counts[item] = have - qty/' inventory.py
