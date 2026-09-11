#!/usr/bin/env bash
# Starts a local HydraDB graph-node (github.com/hydra-db/hydradb quick-start).
# Idempotent-ish: skips if a container named "hydradb" is already running.
set -euo pipefail
cd "$(dirname "$0")/.."

if docker ps --format '{{.Names}}' | grep -qx hydradb; then
  echo "hydradb container already running."
  exit 0
fi

mkdir -p hydradb-data/store hydradb-data/cache
if [ ! -f hydradb-data/auth-token ]; then
  printf '%s\n' 'local-development-token-32-bytes' > hydradb-data/auth-token
fi

docker rm -f hydradb >/dev/null 2>&1 || true
docker run -d --name hydradb \
  --user "$(id -u):$(id -g)" \
  -p 7687:7687 -p 8443:8443 -p 9090:9090 \
  -v "$PWD/hydradb-data:/data" \
  -e CLOUD_PROVIDER=local \
  -e LOCAL_PATH=/data/store \
  -e GRAPH_NAMESPACE=default \
  -e GRAPH_ID=default \
  -e GRAPH_CELL_ID=cell-0 \
  -e GRAPH_CELLS=cell-0 \
  -e GRAPH_NODE_ID=node-0 \
  -e GRAPH_BOLT_NODE_ADDRESSES=node-0=127.0.0.1:7687 \
  -e GRAPH_ADVERTISED_BOLT_ADDR=127.0.0.1:7687 \
  -e GRAPH_DATA_CACHE_DIR=/data/cache \
  -e GRAPH_AUTH_TOKEN_FILE=/data/auth-token \
  -e GRAPH_ALLOW_PLAINTEXT=true \
  -e RUST_MIN_STACK=33554432 \
  ghcr.io/hydra-db/hydradb:latest

echo "HydraDB starting — HTTP API on http://127.0.0.1:8443, Bolt on 7687."
