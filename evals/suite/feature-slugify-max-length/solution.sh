set -euo pipefail
python3 - <<'EOF'
from pathlib import Path
p = Path("textutils.py")
s = p.read_text()
s = s.replace('def slugify(text, separator="-"):', 'def slugify(text, separator="-", max_length=None):')
s = s.replace(
    "    return _SEPARATORS.sub(separator, text).strip(separator)\n",
    "    slug = _SEPARATORS.sub(separator, text).strip(separator)\n"
    "    if max_length is None or len(slug) <= max_length:\n"
    "        return slug\n"
    "    words = slug.split(separator)\n"
    "    out = words[0][:max_length]\n"
    "    for word in words[1:]:\n"
    "        candidate = out + separator + word\n"
    "        if len(candidate) > max_length:\n"
    "            break\n"
    "        out = candidate\n"
    "    return out\n",
)
p.write_text(s)
EOF
