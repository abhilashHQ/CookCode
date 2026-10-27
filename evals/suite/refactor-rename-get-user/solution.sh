set -euo pipefail
grep -rl --include='*.py' 'get_usr' . | xargs sed -i 's/\bget_usr\b/get_user/g'
