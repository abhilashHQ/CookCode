import sys

from registry import COMMANDS


def main(argv):
    command = COMMANDS.get(argv[0] if argv else "help")
    if command is None:
        print(f"unknown command {argv[0]}")
        return 2
    print(command(argv[1:]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
