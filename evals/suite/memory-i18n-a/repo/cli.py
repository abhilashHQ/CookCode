import sys

from i18n import t


def main(argv):
    if argv[:1] == ["greet"]:
        print(t("greeting", name=argv[1]))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
