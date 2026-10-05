#!/bin/sh
# Installs, updates and starts pulserver's virtual scanner in Docker for
# MaRGE's page, with the scanner settings the page wrote into this file:
#
#   sh pulserver.sh
#
# Run it again to apply new settings or to take a newer image.
IMAGE=ghcr.io/pulserver/pulserver
NAME=pulserver
PAGE='@PAGE@'
SEQUENCES='@SEQUENCES@'
RECON='@RECON@'
HOME_DIR=${PULSERVER_HOME:-$HOME/.pulserver}

if ! command -v docker >/dev/null 2>&1; then
    echo "Docker is not installed: install it from https://docs.docker.com/get-started/get-docker/ and run this again."
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    echo "Docker is installed but not running: start Docker Desktop, or the Docker service, and run this again."
    exit 1
fi

mkdir -p "$HOME_DIR"
cat > "$HOME_DIR/limits.txt" <<'LIMITS'
[Limits]
@LIMITS@
[Limits End]
LIMITS

before=$(docker image inspect --format '{{.Id}}' "$IMAGE" 2>/dev/null)
if [ -z "$before" ]; then
    echo "pulserver is not installed: downloading $IMAGE."
else
    echo "pulserver is installed: checking for a newer version."
fi
if ! docker pull "$IMAGE"; then
    if [ -z "$before" ]; then
        echo "pulserver could not be downloaded."
        exit 1
    fi
    echo "The newest version could not be checked: starting the installed one."
fi
after=$(docker image inspect --format '{{.Id}}' "$IMAGE" 2>/dev/null)
if [ -z "$before" ]; then
    echo "pulserver is installed."
elif [ "$before" = "$after" ]; then
    echo "pulserver is up to date."
else
    echo "pulserver was outdated and is updated."
fi

digest=$(docker image inspect --format '{{index .RepoDigests 0}}' "$IMAGE" 2>/dev/null)
set -- -e "PULSERVER_IMAGE=$digest" -v "$HOME_DIR/limits.txt:/console/limits.txt:ro"
[ -n "$SEQUENCES" ] && set -- "$@" -v "$SEQUENCES:/console/user/plugins:ro"
[ -n "$RECON" ] && set -- "$@" -v "$RECON:/console/user/recon:ro"
docker rm -f "$NAME" >/dev/null 2>&1
docker run -d --restart unless-stopped --name "$NAME" -p 127.0.0.1:8765:8765 "$@" "$IMAGE" >/dev/null || exit 1
echo "pulserver is running with these settings."

if [ -n "$PAGE" ]; then
    if command -v open >/dev/null 2>&1; then open "$PAGE"
    elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$PAGE" >/dev/null 2>&1
    else echo "Open $PAGE"
    fi
fi
