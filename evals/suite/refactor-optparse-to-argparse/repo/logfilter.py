"""Filter application log lines by level, logger and time window.

Log format: ``2024-04-01T12:00:00 INFO app.db: message``
"""
import optparse
import sys
from datetime import datetime

LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def parse_line(line):
    stamp, level, rest = line.split(None, 2)
    logger, _, message = rest.partition(": ")
    return {"time": datetime.fromisoformat(stamp), "level": level, "logger": logger, "message": message.rstrip("\n")}


def keep(entry, min_level, loggers, since):
    if LEVELS.index(entry["level"]) < LEVELS.index(min_level):
        return False
    if loggers and not any(entry["logger"] == n or entry["logger"].startswith(n + ".") for n in loggers):
        return False
    if since is not None and entry["time"] < since:
        return False
    return True


def build_parser():
    parser = optparse.OptionParser(usage="%prog [options] [FILE...]", prog="logfilter")
    parser.add_option("-l", "--level", type="choice", choices=LEVELS, default="INFO",
                      help="minimum level to show [default: %default]")
    parser.add_option("-g", "--logger", action="append", dest="loggers", default=[],
                      help="only show this logger and its children (repeatable)")
    parser.add_option("--since", metavar="ISO_TIME", help="drop lines before this timestamp")
    parser.add_option("-c", "--count", action="store_true", default=False,
                      help="print the number of matching lines instead of the lines")
    return parser


def main(argv=None):
    parser = build_parser()
    options, files = parser.parse_args(argv)
    since = None
    if options.since:
        try:
            since = datetime.fromisoformat(options.since)
        except ValueError:
            parser.error(f"invalid --since value: {options.since}")

    streams = [open(path, encoding="utf-8") for path in files] or [sys.stdin]
    matched = 0
    try:
        for stream in streams:
            for line in stream:
                if not line.strip():
                    continue
                if keep(parse_line(line), options.level, options.loggers, since):
                    matched += 1
                    if not options.count:
                        sys.stdout.write(line if line.endswith("\n") else line + "\n")
    finally:
        for stream in streams:
            if stream is not sys.stdin:
                stream.close()
    if options.count:
        print(matched)
    return 0


if __name__ == "__main__":
    sys.exit(main())
