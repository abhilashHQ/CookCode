"""Usage: python release.py CURRENT_VERSION {major,minor,patch}"""
import sys

from versioning import Version


def next_version(current, part):
    return str(Version.parse(current).bump(part))


def main(argv):
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    print(next_version(*argv))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
