#!/usr/bin/env bash
# Recreates the local Cognee container with an LLM configured so
# cognify()/add() can actually run entity/relationship extraction
# (currently missing — `add_document()` fails with LLMAPIKeyNotSetError
# without this). Defaults to local Ollama (see .env) — no external API
# key or network access needed at the venue.
#
# Usage:
#   1. Make sure Ollama is running locally with the models in .env pulled:
#        ollama list   # expects mistral:latest, nomic-embed-text:latest
#   2. bash scripts/configure_cognee_llm.sh
#
# To use a hosted provider (OpenAI/Anthropic/etc.) instead, just change
# LLM_PROVIDER/LLM_MODEL/LLM_API_KEY (and drop LLM_ENDPOINT) in .env —
# see https://docs.cognee.ai/setup-configuration/llm-providers
#
# NOTE: the current container has no volume mount, so anything already
# ingested lives only in the container's own filesystem. This script keeps
# the same container name/port and adds a named volume (cognee-data) going
# forward so a future recreate doesn't lose the graph again.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a

if [ "${LLM_PROVIDER:-}" = "ollama" ]; then
  if ! curl -s -o /dev/null -m 3 "http://localhost:11434/api/tags" 2>/dev/null; then
    echo "Warning: couldn't reach Ollama on localhost:11434 — is it running? ('ollama serve')" >&2
  fi
elif [ -z "${LLM_API_KEY:-}" ]; then
  echo "LLM_API_KEY is empty in .env — fill it in first, or set LLM_PROVIDER=ollama" >&2
  echo "to use your local Ollama instead." >&2
  exit 1
fi

docker rm -f cognee >/dev/null 2>&1 || true
docker volume create cognee-data >/dev/null

docker run -d --name cognee \
  -p 8000:8000 \
  -v cognee-data:/app/.cognee_system \
  -e LLM_PROVIDER="${LLM_PROVIDER:-ollama}" \
  -e LLM_MODEL="${LLM_MODEL:-mistral:latest}" \
  -e LLM_API_KEY="${LLM_API_KEY:-ollama}" \
  ${LLM_ENDPOINT:+-e LLM_ENDPOINT="${LLM_ENDPOINT}"} \
  ${EMBEDDING_PROVIDER:+-e EMBEDDING_PROVIDER="${EMBEDDING_PROVIDER}"} \
  ${EMBEDDING_MODEL:+-e EMBEDDING_MODEL="${EMBEDDING_MODEL}"} \
  ${EMBEDDING_ENDPOINT:+-e EMBEDDING_ENDPOINT="${EMBEDDING_ENDPOINT}"} \
  ${EMBEDDING_DIMENSIONS:+-e EMBEDDING_DIMENSIONS="${EMBEDDING_DIMENSIONS}"} \
  ${HUGGINGFACE_TOKENIZER:+-e HUGGINGFACE_TOKENIZER="${HUGGINGFACE_TOKENIZER}"} \
  cognee/cognee:main

echo "Waiting for Cognee to become healthy..."
for i in $(seq 1 30); do
  status=$(docker inspect --format '{{.State.Health.Status}}' cognee 2>/dev/null || echo "starting")
  if [ "$status" = "healthy" ]; then
    echo "Cognee is healthy on http://localhost:8000"
    exit 0
  fi
  sleep 2
done
echo "Still not healthy after 60s — check: docker logs cognee" >&2
exit 1
