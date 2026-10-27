#!/usr/bin/env bash
# Usage: evals/run_tb.sh harness|terminus-2 ATTEMPTS [task ...]
# Both agents get the same endpoint, model and turn cap. Defaults to the slice in tb-slice.txt.
set -euo pipefail
agent=$1
attempts=$2
shift 2
tasks=("$@")
if [ ${#tasks[@]} -eq 0 ]; then
    mapfile -t tasks < <(grep -v '^#' "$(dirname "$0")/tb-slice.txt")
fi
model=minimaxai/minimax-m3.1-flash
args=(
    -p "${TB_DATASET:-$HOME/scratch/tb/terminal-bench}"
    -k "$attempts"
    -n "${TB_CONCURRENCY:-2}"
    -o "${TB_JOBS:-$HOME/scratch/tb/jobs}"
    --job-name "$agent-$(date -u +%Y%m%d-%H%M%S)"
)
for task in "${tasks[@]}"; do
    args+=(-i "$task")
done
case $agent in
    harness)
        uv run harbor run "${args[@]}" -a harness.harbor.agent:HarnessAgent \
            -m "nim/$model" --ak max_turns=50
        ;;
    terminus-2)
        OPENAI_API_KEY=$NVIDIA_API_KEY uv run harbor run "${args[@]}" -a terminus-2 \
            -m "openai/$model" --ak api_base=https://integrate.api.nvidia.com/v1 --ak max_turns=50
        ;;
    *)
        echo "unknown agent $agent" >&2
        exit 2
        ;;
esac
