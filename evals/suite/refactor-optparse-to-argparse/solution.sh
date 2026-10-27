set -euo pipefail
python3 - <<'EOF'
from pathlib import Path
p = Path("logfilter.py")
s = p.read_text()
s = s.replace("import optparse\n", "import argparse\n")
start = s.index("def build_parser():")
end = s.index("def main(argv=None):")
s = s[:start] + '''def build_parser():
    parser = argparse.ArgumentParser(prog="logfilter", description="Filter application log lines.")
    parser.add_argument("files", nargs="*", metavar="FILE")
    parser.add_argument("-l", "--level", choices=LEVELS, default="INFO",
                        help="minimum level to show (default: %(default)s)")
    parser.add_argument("-g", "--logger", action="append", dest="loggers", default=[],
                        help="only show this logger and its children (repeatable)")
    parser.add_argument("--since", metavar="ISO_TIME", help="drop lines before this timestamp")
    parser.add_argument("-c", "--count", action="store_true",
                        help="print the number of matching lines instead of the lines")
    return parser


''' + s[end:]
s = s.replace("    options, files = parser.parse_args(argv)\n", "    options = parser.parse_args(argv)\n    files = options.files\n")
p.write_text(s)
EOF
