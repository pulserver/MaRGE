#!/bin/sh
# Installs, updates and starts pulserver's virtual scanner in Docker for
# MaRGE's page, with the scanner settings the page wrote into this file:
#
#   sh pulserver.sh
#
# Run so, it also copies itself to ~/.pulserver and registers that copy as the
# handler of pulserver: links, which the page's Start button opens with its
# settings: pulserver:start/x<limits>.x<sequences>.x<recon>, each part
# base64url behind an x. Run with such a link, it takes the settings from it
# and writes its messages to ~/.pulserver/launcher.log.
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

decode() {
    part=$(printf '%s' "${1#x}" | tr '_-' '/+')
    printf '%s' "$part" | base64 -d 2>/dev/null || printf '%s' "$part" | base64 -D
}

install_handler() {
    cp "$0" "$HOME_DIR/pulserver.sh" 2>/dev/null
    if [ "$(uname)" = Darwin ]; then
        app="$HOME/Applications/pulserver.app"
        mkdir -p "$HOME/Applications"
        rm -rf "$app"
        cat > "$HOME_DIR/handler.applescript" <<SCRIPT
on open location address
	do shell script "PATH=\$HOME/.docker/bin:/usr/local/bin:/opt/homebrew/bin:\$PATH; sh " & quoted form of "$HOME_DIR/pulserver.sh" & " " & quoted form of address & " >/dev/null 2>&1 &"
end open location
SCRIPT
        osacompile -o "$app" "$HOME_DIR/handler.applescript" || return
        /usr/libexec/PlistBuddy -c 'Add :LSUIElement bool true' -c 'Add :CFBundleURLTypes array' \
            -c 'Add :CFBundleURLTypes:0 dict' -c 'Add :CFBundleURLTypes:0:CFBundleURLName string pulserver' \
            -c 'Add :CFBundleURLTypes:0:CFBundleURLSchemes array' \
            -c 'Add :CFBundleURLTypes:0:CFBundleURLSchemes:0 string pulserver' "$app/Contents/Info.plist"
        codesign --force --sign - "$app" 2>/dev/null
        /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$app"
    else
        apps=${XDG_DATA_HOME:-$HOME/.local/share}/applications
        mkdir -p "$apps"
        cat > "$apps/pulserver.desktop" <<ENTRY
[Desktop Entry]
Type=Application
Name=pulserver
Exec=sh "$HOME_DIR/pulserver.sh" %u
NoDisplay=true
MimeType=x-scheme-handler/pulserver;
ENTRY
        command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$apps" 2>/dev/null
        command -v xdg-mime >/dev/null 2>&1 && xdg-mime default pulserver.desktop x-scheme-handler/pulserver
    fi
    echo "Start pulserver on the page now starts it with the page's settings."
}

mkdir -p "$HOME_DIR"
case $1 in
pulserver:start/*)
    exec >"$HOME_DIR/launcher.log" 2>&1
    parts=${1#pulserver:start/}
    decode "${parts%%.*}" > "$HOME_DIR/limits.txt"
    parts=${parts#*.}
    SEQUENCES=$(decode "${parts%%.*}")
    RECON=$(decode "${parts#*.}")
    PAGE=
    ;;
*)
    cat > "$HOME_DIR/limits.txt" <<'LIMITS'
[Limits]
@LIMITS@
[Limits End]
LIMITS
    install_handler
    ;;
esac

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
