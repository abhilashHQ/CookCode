set -euo pipefail
python3 - <<'EOF'
from pathlib import Path
p = Path("wordstats.py")
s = p.read_text()
s = s.replace("import argparse\nimport sys\n", "import argparse\nimport json\nimport sys\n")
s = s.replace(
    '    parser.add_argument("files", nargs="*", help="files to read (default: stdin)")\n',
    '    parser.add_argument("files", nargs="*", help="files to read (default: stdin)")\n'
    '    parser.add_argument("--format", choices=("table", "json"), default="table")\n',
)
s = s.replace(
    "    results = collect(args.files)\n",
    "    results = collect(args.files)\n"
    '    if args.format == "json":\n'
    "        total = {k: sum(stats[k] for _, stats in results) for k in FIELDS}\n"
    '        files = [{"name": name, **stats} for name, stats in results]\n'
    '        print(json.dumps({"files": files, "total": total}))\n'
    "        return 0\n",
)
p.write_text(s)
EOF
