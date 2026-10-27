def run(args):
    from registry import COMMANDS

    return "commands: " + ", ".join(sorted(COMMANDS))
