#!/usr/bin/env bash
# Run the STT benchmark on a remote NVIDIA host and merge results back.
# Usage: bench/scripts/run_remote.sh <ssh-host> [remote-venv-path]
set -euo pipefail

HOST="${1:?usage: run_remote.sh <ssh-host> [remote-venv]}"
RVENV="${2:-~/.venvs/whisper_aloud_bench}"
RDIR="~/whisperaloud-bench"

echo ">> creating remote dirs on $HOST"
ssh "$HOST" "mkdir -p $RDIR/bench $RDIR/src"

echo ">> syncing bench code + manifests to $HOST (no audio/models)"
rsync -az --delete \
  --exclude 'datasets/audio' --exclude 'sherpa-onnx-*' --exclude 'results' \
  ./bench/ "$HOST:$RDIR/bench/"
rsync -az ./src/ "$HOST:$RDIR/src/"

echo ">> remote setup + GPU sanity check (fails loudly if no CUDA)"
ssh "$HOST" bash -lc "'
  set -euo pipefail
  cd $RDIR
  python3 -m venv $RVENV 2>/dev/null || true
  source $RVENV/bin/activate
  pip install -q -r bench/requirements-bench.txt
  # GPU-enabled sherpa-onnx (default pip wheel is CPU-only -> Parakeet would fall back to CPU).
  # If this index install fails or mismatches CUDA, the runner VRAM guard skips parakeet:cuda.
  pip install -q sherpa-onnx -f https://k2-fsa.github.io/sherpa/onnx/cuda.html || true
  nvidia-smi --query-gpu=name --format=csv,noheader || { echo \"NO NVIDIA GPU on $HOST\"; exit 3; }
  export PYTHONPATH=$RDIR/src
  python -m bench.datasets.fleurs --lang es,en --n 40
  test -d bench/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8 || (
    curl -sL -o m.tar.bz2 https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2 &&
    tar xjf m.tar.bz2 -C bench/ && rm m.tar.bz2 )
  python -m bench.runner --lang es,en \
    --engines \"parakeet:bench/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8,faster-whisper:small,faster-whisper:medium\" \
    --device cuda --reps 3 --hw-id $HOST
'"

echo ">> pulling remote ledger rows and merging into local ledger"
ssh "$HOST" "cat $RDIR/bench/results/ledger.jsonl" >> bench/results/ledger.jsonl
python -m bench.report
echo ">> done. Review bench/results/ledger.jsonl for duplicates before committing."
