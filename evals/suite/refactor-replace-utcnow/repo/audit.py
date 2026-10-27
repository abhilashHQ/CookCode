from datetime import datetime

STAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def stamp():
    return datetime.utcnow().strftime(STAMP_FORMAT)


def record(log, actor, action, when=None):
    when = when or datetime.utcnow()
    entry = {"at": when.strftime(STAMP_FORMAT), "actor": actor, "action": action}
    log.append(entry)
    return entry


def parse_stamp(text):
    return datetime.strptime(text, STAMP_FORMAT)


def since(log, cutoff):
    return [e for e in log if parse_stamp(e["at"]) >= cutoff]
