#!/bin/sh
# Ookami installer: curl -fsSL https://raw.githubusercontent.com/KatyarAILabs/Ookami/main/install.sh | sh
#
# Installs uv (if missing), then Ookami as a uv tool from GitHub with the gateway, then the inference engine
# for this machine: MLX on Apple silicon, vLLM on NVIDIA. CPU-only machines use llama.cpp, which you install
# yourself. Nothing is run as root.
#
#   OOKAMI_REF=v0.3.0   install a tag or branch instead of main
#   OOKAMI_NO_ENGINE=1  skip the engine
set -eu

REF="${OOKAMI_REF:-main}"
REPO="https://github.com/KatyarAILabs/Ookami.git"

say() { printf '\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*" >&2; }

if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv (Python package manager)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

os=$(uname -s); arch=$(uname -m)
if [ "$os" = "Darwin" ] && [ "$arch" = "arm64" ]; then
  kind="Apple silicon"; engine="mlx-lm"
elif command -v nvidia-smi >/dev/null 2>&1; then
  kind="NVIDIA GPU"; engine="vllm"
else
  kind="CPU only"; engine=""
fi

say "Installing Ookami (${REF}) for ${kind}…"
with=""
if [ -n "$engine" ] && [ -z "${OOKAMI_NO_ENGINE:-}" ]; then
  with="--with $engine"
fi
# shellcheck disable=SC2086
uv tool install --force --python 3.12 $with "ookami[gateway] @ git+$REPO@$REF"

if ! command -v ookami >/dev/null 2>&1; then
  warn "ookami was installed to $(uv tool dir --bin 2>/dev/null || echo ~/.local/bin), which isn't on your PATH yet."
  warn "Run: uv tool update-shell   (then open a new terminal)"
fi

say "Done. Next:"
cat <<'EOF'
  ookami init                                  # writes ookami.yaml for this machine
  ookami up                                    # model + gateway on :4000
  export OOKAMI_API_KEY=$(ookami keys master)
  curl localhost:4000/v1/chat/completions -H "Authorization: Bearer $OOKAMI_API_KEY" \
    -H 'Content-Type: application/json' -d '{"model": "local", "messages": [{"role": "user", "content": "hi"}]}'
EOF
if [ -z "$engine" ]; then
  warn "No GPU found: install llama.cpp (brew install llama.cpp, or a release from github.com/ggml-org/llama.cpp)."
  warn "ookami init writes a config that uses it."
fi
