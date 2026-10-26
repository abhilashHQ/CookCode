# 0030. Exec semantics are rebuilt inside the container, not borrowed from Harbor

Status: accepted (M4)
Date: 2026-10-26

## Context
Our `Environment.exec` promises a process group per command, stdin closed, output capped (head and tail kept), and the whole group killed on timeout or cancel (0006, 0007). Harbor's `BaseEnvironment.exec(command, cwd, env, timeout_sec, user)` runs `docker compose exec ... bash -c` and returns the full output. Reading its source showed the gap: on timeout it kills the docker client process, and a process started by `docker exec` keeps running in the container when its client dies. It has no cancellation and no output cap.

## Options considered
- Use Harbor's exec as is: timeouts would leak processes into the container. A runaway `sleep` or server would outlive the tool call and change later results.
- Run a small agent daemon inside the container: full control, but an install step per task image, and the daemon itself can die.
- Wrap every command in a short shell script and use a second exec to kill.

## Decision
`env/harbor.py:HarborEnvironment` wraps each command:
1. `setsid bash -c` starts it in a new session. The inner shell writes `$$` (the group id) to a pidfile in a per-call directory, changes to the working directory, and `eval`s the command with stdin from `/dev/null` and stdout and stderr in one file.
2. After it exits, the script kills any group members left behind (the local environment does the same), then prints a header with the exit code and byte count, followed by the output base64-encoded. When the output is over the cap it sends the first and last halves, and `render_capped` (shared with the local environment) adds the truncation notice.
3. The host side races the exec against the cancel token and the timeout. On either, a second exec waits briefly for the pidfile and runs `kill -KILL -- -<pgid>`. The first exec then returns whatever output was captured.

Harbor's own `timeout_sec` is set 60 seconds past ours, as a backstop. File reads use `base64` through exec. Writes go through `upload_file` to a temp file next to the resolved target, then `mv`, because one argv string is capped at 128 KiB on Linux. An existing file keeps its mode and owner.

The adapter depends on a structural `Container` protocol (exec and upload_file), not on harbor's classes.

## Consequences
- A fake container that runs execs as local processes, and like docker kills only the client, proves the semantics in CI. The output is byte-identical to `LocalEnvironment`'s, truncation included, and timeout, cancel and leftover children all end with the group dead.
- Measured on the `fix-git` run: 0.12 to 0.73 s per tool call including docker exec overhead, 7 s of tools in a 442 s run.
- It assumes bash, `setsid`, `base64 -w0`, `realpath -m` and `/tmp` in the image (GNU userland). That holds for the Terminal-Bench images we have run. A busybox image would need a fallback.
- A process that calls `setsid` itself escapes the kill, same as locally.

## What would change it
An image without bash or coreutils, or a Harbor exec that grows real cancellation, which would let the wrapper shrink.
