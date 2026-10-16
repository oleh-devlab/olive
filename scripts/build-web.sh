#!/usr/bin/env bash
# Builds the schedule web editor's page from web/ into src/static/schedule/,
# which is where the bot serves it from. Run it again after every pull that
# changes web/.
#
# The build runs in a Node container, so the host needs no Node of its own:
# podman if it answers, docker otherwise, and the host's own npm only when
# neither does. CONTAINER_RUNTIME=podman, docker or none picks one instead.
#
# Whichever runs it, the result is the same: one image, one lockfile, and
# files owned by whoever ran the script rather than by a container's root.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
web="$root/web"
out="$root/src/static/schedule"
node_major="$(tr -d '[:space:]' <"$web/.node-version")"
image="docker.io/library/node:${node_major}-alpine"
build="npm ci --no-audit --no-fund && npm run build"

answers() {
    command -v "$1" >/dev/null && "$1" info >/dev/null 2>&1
}

pick_runtime() {
    if [[ -n "${CONTAINER_RUNTIME:-}" ]]; then
        echo "$CONTAINER_RUNTIME"
    elif answers podman; then
        echo podman
    elif answers docker; then
        echo docker
    else
        echo none
    fi
}

# In a rootless runtime the container's root already is the user who started
# it, so its files come out theirs. A rootful one has to be told who to be.
# `docker` may well be podman under another name, so ask what it is.
rootless() {
    if [[ "$("$1" --version 2>/dev/null)" == *podman* ]]; then
        [[ "$("$1" info --format '{{.Host.Security.Rootless}}')" == true ]]
    else
        [[ "$("$1" info --format '{{.SecurityOptions}}')" == *rootless* ]]
    fi
}

build_in_container() {
    local runtime="$1"
    # Spelled out below because macOS's bash 3 calls an empty array unbound.
    local user=()
    rootless "$runtime" || user=(--user "$(id -u):$(id -g)")

    # Docker creates a missing mount source owned by root; podman refuses one.
    mkdir -p "$out"
    echo "Building the schedule page with $runtime in $image"
    # label=disable rather than :z, so that on an SELinux host the source tree
    # keeps its own labels instead of being relabelled for containers.
    "$runtime" run --rm ${user[@]+"${user[@]}"} \
        --security-opt label=disable \
        -v "$web:/repo/web" \
        -v "$out:/repo/src/static/schedule" \
        -w /repo/web \
        -e HOME=/tmp \
        -e npm_config_cache=/repo/web/.npm \
        -e npm_config_update_notifier=false \
        "$image" sh -c "$build"
}

build_on_host() {
    if ! command -v npm >/dev/null; then
        echo "Neither podman nor docker is running and there is no npm: install one of them to build the page." >&2
        exit 1
    fi
    local major
    major="$(node --version | sed -E 's/^v([0-9]+).*/\1/')"
    if ((major < node_major)); then
        echo "Node $node_major or newer is needed to build the page, and this host has $(node --version)." >&2
        exit 1
    fi
    echo "Building the schedule page with the host's npm (Node $(node --version))"
    (cd "$web" && sh -c "$build")
}

runtime="$(pick_runtime)"
if [[ "$runtime" == none ]]; then
    build_on_host
else
    build_in_container "$runtime"
fi
echo "The page is in ${out#"$root"/}/"
