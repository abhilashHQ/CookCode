set -euo pipefail
python3 - <<'EOF'
from pathlib import Path
p = Path("bin/backup.sh")
s = p.read_text()
s = s.replace(
    'for f in $(ls "$src"); do\n    cp -p $src/$f "$target/"\n    count=$((count + 1))\ndone\n',
    'for path in "$src"/*; do\n'
    '    if [ -f "$path" ]; then\n'
    '        cp -p -- "$path" "$target/"\n'
    '        count=$((count + 1))\n'
    '    fi\n'
    'done\n',
)
s = s.replace(
    'ls -1d "$dest"/*/ | sort | head -n -"$keep" | xargs -r rm -rf\n',
    'mapfile -t snapshots < <(find "$dest" -mindepth 1 -maxdepth 1 -type d | sort)\n'
    'excess=$(( ${#snapshots[@]} - keep ))\n'
    'for (( i = 0; i < excess; i++ )); do\n'
    '    rm -rf -- "${snapshots[$i]}"\n'
    'done\n',
)
p.write_text(s)
EOF
grep -q mapfile bin/backup.sh
