# 0031. Harbor adapter on the host, Terminal-Bench 2.0 on Harbor 0.25.0

Status: accepted (M4)
Date: 2026-10-26

## Context
M4 accepts when the oracle run passes and one real task runs end to end in WSL2. The gate question was which Terminal-Bench version to pin. Harbor runs an agent either inside the task container (installed agents such as Codex or Claude Code) or on the host against `environment.exec` (Terminus 2).

## Options considered
- Install our harness into each task container: needs Python 3.12 and our dependencies in arbitrary images, and puts the API key inside the container.
- Run on the host as a `BaseAgent` subclass: one install, the key stays on the host, and every action goes through `HarborEnvironment` (0030).

## Decision
`harness.harbor.agent:HarnessAgent` runs the normal loop on the host in `yolo` mode (the container is the sandbox). The model is given as `<preset>/<model>`, for example `nim/minimaxai/minimax-m3.1-flash`. Model parsing is skipped, because LiteLLM does not know the NIM presets. The workspace root is the container's working directory. The session log and checkpoints go in the trial's `logs_dir`, `trajectory.json` is written in a `finally` (so a Harbor timeout still leaves one), and `AgentContext` gets input (including cache), cache and output token counts.

Pinned: dataset `terminal-bench@2.0` (89 tasks), Harbor `0.25.0`, ATIF `v1.8`. Harbor is an optional extra (`harness[harbor]`) and a dev dependency.

Docker: Docker Desktop with WSL integration for Ubuntu (29.9.2 engine, 16 CPUs, 7.6 GB), not Docker Engine inside WSL as the spec's decision 12 said. It needed no sudo, and both runs passed on it.

## Consequences
Acceptance, on 2026-10-26:
- **Oracle:** `fix-git`, reward 1.0, 33 s.
- **Our agent:** `fix-git` with NIM MiniMax M3.1 Flash, reward 1.0, no exceptions, 16 steps. It found the lost commit in the reflog, merged it, resolved the conflict and committed. The run used 182,075 prompt tokens (166,016 cached) and 4,357 output tokens. 435 s of the 442 s went to model latency, and four 429s were retried and logged.
- The free endpoint's latency, not the harness, sets the pace. A run of 5 attempts on several tasks (the spec's protocol) is an M6 job with a faster or paid endpoint.
- The Harbor CLI warns that LiteLLM cannot resolve the model name. That is expected, and the warning is harmless.

## What would change it
A Terminal-Bench release we want to compare against published numbers, or tasks whose images lack the userland 0030 assumes.
