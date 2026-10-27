"""Count lines, words and characters in text files, like a small wc."""
import argparse
import sys

FIELDS = ("lines", "words", "chars")


def count(text):
    return {"lines": text.count("\n"), "words": len(text.split()), "chars": len(text)}


def format_row(name, stats):
    return f"{stats['lines']:>7} {stats['words']:>7} {stats['chars']:>7} {name}"


def collect(paths):
    results = []
    if not paths:
        results.append(("-", count(sys.stdin.read())))
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            results.append((path, count(fh.read())))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(prog="wordstats", description=__doc__)
    parser.add_argument("files", nargs="*", help="files to read (default: stdin)")
    args = parser.parse_args(argv)

    results = collect(args.files)
    for name, stats in results:
        print(format_row(name, stats))
    if len(results) > 1:
        total = {k: sum(stats[k] for _, stats in results) for k in FIELDS}
        print(format_row("total", total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
