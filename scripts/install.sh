#!/usr/bin/env bash
# shellcheck disable=SC2059
# ──────────────────────────────────────────────────────────────────────────────
# OpenGrab Installer — TUI setup wizard (dialog/whiptail)
#
# Three installation modes:
#   1. Docker      — docker compose up (recommended)
#   2. Bare Metal  — pip install + optional systemd service
#   3. Desktop     — PyInstaller build + optional AppImage
#
# Requires: dialog (preferred) or whiptail (fallback)
#
# Usage:
#     chmod +x install.sh
#     ./install.sh
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_URL="https://github.com/Skydope/OpenGrab.git"
TMPDIR="${TMPDIR:-/tmp}"
TMP_OUT="$TMPDIR/opengrab_install.$$"

# ── Cleanup on exit ──────────────────────────────────────────────────────────
cleanup() {
    rm -f "$TMP_OUT" "$TMP_OUT".* 2>/dev/null || true
}
trap cleanup EXIT

# ── Detect dialog variant ────────────────────────────────────────────────────
DIALOG=""
if command -v dialog &>/dev/null; then
    DIALOG="dialog"
elif command -v whiptail &>/dev/null; then
    DIALOG="whiptail"
else
    echo "=============================================="
    echo " OpenGrab Installer"
    echo "=============================================="
    echo
    echo " This installer needs 'dialog' or 'whiptail'."
    echo
    echo " Install with:"
    echo "   sudo apt install dialog"
    echo "   sudo dnf install dialog"
    echo "   sudo pacman -S dialog"
    echo "=============================================="
    exit 1
fi

# ── Dialog wrappers ──────────────────────────────────────────────────────────

_dialog() {
    # Forward to dialog or whiptail. Both support the same basic options
    # for the subset we use (--title, --msgbox, --yesno, --inputbox, --menu,
    # --gauge, --infobox, --passwordbox, --defaultno).
    if [ "$DIALOG" = "whiptail" ]; then
        whiptail "$@" 2>"$TMP_OUT"
    else
        dialog "$@" 2>"$TMP_OUT"
    fi
}

msgbox() {
    local title="$1" text="$2"
    _dialog --title "$title" --msgbox "$text" 16 64
}

yesno() {
    local title="$1" text="$2" default="$3"
    if [ "$default" = "yes" ]; then
        _dialog --title "$title" --yesno "$text" 12 60
    else
        _dialog --defaultno --title "$title" --yesno "$text" 12 60
    fi
}

inputbox() {
    local title="$1" text="$2" default="$3"
    _dialog --title "$title" --inputbox "$text" 10 60 "$default"
    local rc=$?
    [ $rc -eq 0 ] && cat "$TMP_OUT"
    return $rc
}

passwordbox() {
    local title="$1" text="$2"
    _dialog --title "$title" --passwordbox "$text" 10 60
    local rc=$?
    [ $rc -eq 0 ] && cat "$TMP_OUT"
    return $rc
}

menu_dlg() {
    local title="$1" text="$2"
    shift 2
    local items=$(($# / 2))
    local menu_h=$((items + 7))  # items + padding for title/borders
    _dialog --title "$title" --menu "$text" "$menu_h" 64 "$items" "$@"
}

gauge_show() {
    local title="$1" text="$2"
    # Read percentage values from stdin, 0-100. Exit at 100.
    _dialog --title "$title" --gauge "$text" 8 60 0
}

infobox() {
    local title="$1" text="$2" timeout="${3:-3}"
    _dialog --title "$title" --infobox "$text" 8 60
    sleep "$timeout"
}

# ── Helpers ──────────────────────────────────────────────────────────────────

check_cmd() { command -v "$1" &>/dev/null; }

check_python_version() {
    if ! check_cmd python3; then
        echo "  ✗ python3 >= 3.12 (not found)"
        return 1
    fi
    local ver
    ver=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
    local major minor
    major=$(echo "$ver" | cut -d. -f1)
    minor=$(echo "$ver" | cut -d. -f2)
    if [ "$major" -lt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -lt 12 ]; }; then
        echo "  ✗ python3 >= 3.12 (found $ver)"
        return 1
    fi
    return 0
}

rand_token() { python3 -c "import secrets; print(secrets.token_urlsafe(16))" 2>/dev/null || openssl rand -hex 12; }

detect_pkg_mgr() {
    if check_cmd apt; then echo "apt"
    elif check_cmd dnf; then echo "dnf"
    elif check_cmd pacman; then echo "pacman"
    elif check_cmd zypper; then echo "zypper"
    else echo ""
    fi
}

install_pkg() {
    local pkg="$1"
    local mgr
    mgr=$(detect_pkg_mgr)
    case "$mgr" in
        apt) sudo apt install -y "$pkg" ;;
        dnf) sudo dnf install -y "$pkg" ;;
        pacman) sudo pacman -S --noconfirm "$pkg" ;;
        zypper) sudo zypper install -y "$pkg" ;;
        *) return 1 ;;
    esac
}

is_in_repo() {
    [ -f "pyproject.toml" ] && grep -q 'name = "opengrab"' pyproject.toml 2>/dev/null
}

clone_repo() {
    local dest="$1"
    git clone --depth 1 "$REPO_URL" "$dest" &>/dev/null
}

run_with_gauge() {
    local desc="$1" title="$2"
    shift 2
    # Run "$@" in background, pipe synthetic progress to gauge.
    # Exit code saved to temp file for retrieval after pipe ends.
    (
        echo "0"
        "$@" >"$TMP_OUT.run" 2>&1 &
        local pid=$!
        local pct=0
        while kill -0 $pid 2>/dev/null; do
            sleep 0.5
            pct=$(( (pct + 3) % 100 ))
            echo "$pct"
        done
        wait $pid
        echo "$?" > "$TMP_OUT.rc"
        echo "100"
    ) | gauge_show "$title" "$desc"
    local real_rc=1
    [ -f "$TMP_OUT.rc" ] && real_rc=$(cat "$TMP_OUT.rc")
    return "$real_rc"
}

# ── Config wizard (shared by Docker + Bare Metal) ────────────────────────────

configure_env() {
    local outfile="$1"
    local token port download_dir max_jobs max_total_mb autoupdate

    # Token
    if yesno "Configuration" "Set a custom access token?\n\nIf you skip, one will be auto-generated." "no"; then
        token=$(passwordbox "Configuration" "Enter access token (or leave empty to generate):")
        if [ -z "$token" ]; then
            token=$(rand_token)
        fi
    else
        token=$(rand_token)
    fi
    msgbox "Configuration" "Your access token:\n\n    $token\n\nSave this — you'll need it to log in."

    # Port
    port=$(inputbox "Configuration" "HTTP port:" "8800")
    [ -z "$port" ] && port=8800

    # Download dir
    download_dir=$(inputbox "Configuration" "Download directory:" "$HOME/Downloads/OpenGrab")
    [ -z "$download_dir" ] && download_dir="$HOME/Downloads/OpenGrab"

    # Max jobs
    max_jobs=$(inputbox "Configuration" "Max concurrent downloads:" "2")
    [ -z "$max_jobs" ] && max_jobs=2

    # Disk budget
    if yesno "Configuration" "Set a total disk budget?" "no"; then
        max_total_mb=$(inputbox "Configuration" "Max total storage (MB):" "0")
        [ -z "$max_total_mb" ] && max_total_mb=0
    else
        max_total_mb=0
    fi

    # Auto-update
    if yesno "Configuration" "Auto-update yt-dlp on start?\n\nWarning: this pulls the latest version from PyPI unpinned on every container restart." "no"; then
        autoupdate=1
    else
        autoupdate=0
    fi

    # Write .env
    cat > "$outfile" <<EOF
# OpenGrab — environment configuration
# Generated by install.sh

OPENGRAB_TOKEN=$token
OPENGRAB_PORT=$port
OPENGRAB_DIR=$download_dir
OPENGRAB_MAX_JOBS=$max_jobs
OPENGRAB_MAX_TOTAL_MB=$max_total_mb
OPENGRAB_AUTOUPDATE=$autoupdate
OPENGRAB_MAX_SIZE_MB=0
EOF
    echo "$port" > "$TMP_OUT.port"
    echo "$token" > "$TMP_OUT.token"
}

# ── Mode 1: Docker ───────────────────────────────────────────────────────────

mode_docker() {
    # Prerequisites
    local errs=""
    check_cmd docker || errs="$errs\n  ✗ Docker (required)"
    check_cmd git || errs="$errs\n  ✗ Git (required)"

    if [ -n "$errs" ]; then
        msgbox "Prerequisites" "Missing requirements:$errs\n\nInstall them and retry."
        return 1
    fi

    # Repo detection
    local repo
    if is_in_repo; then
        repo="$PWD"
        msgbox "Repository" "Found OpenGrab repo at:\n$repo"
    else
        repo="$PWD/opengrab"
        infobox "Cloning" "Cloning OpenGrab repository..."
        if ! clone_repo "$repo"; then
            msgbox "Error" "Failed to clone repository.\nCheck your internet connection."
            return 1
        fi
    fi

    # Config
    configure_env "$repo/.env"
    local port token
    port=$(cat "$TMP_OUT.port")
    token=$(cat "$TMP_OUT.token")

    # Build & start
    infobox "Docker" "Building and starting OpenGrab via Docker Compose..."
    if ! run_with_gauge "Starting container..." "Docker Compose" \
        docker compose -f "$repo/docker-compose.yml" up -d --build 2>&1; then
        msgbox "Error" "Docker Compose failed.\nRun 'docker compose up' manually in:\n$repo"
        return 1
    fi

    # Success
    msgbox "Installation Complete" \
        "OpenGrab is running!\n\nURL:   http://localhost:$port\nToken: $token\n\nSave the token — you'll need it to log in."
}

# ── Mode 2: Bare Metal ───────────────────────────────────────────────────────

mode_baremetal() {
    local errs=""
    check_python_version || errs="$errs\n  ✗ python3 >= 3.12 (required)"
    check_cmd pip3 && true || check_cmd pip || errs="$errs\n  ✗ pip (required)"
    check_cmd git || errs="$errs\n  ✗ Git (required)"

    if [ -n "$errs" ]; then
        msgbox "Prerequisites" "Missing requirements:$errs\n\nInstall them and retry."
        return 1
    fi

    # ffmpeg
    if ! check_cmd ffmpeg; then
        if yesno "ffmpeg" "ffmpeg was not found. It is required for video processing.\n\nInstall it now?" "yes"; then
            install_pkg ffmpeg || msgbox "Warning" "Could not install ffmpeg.\nDownloads may fail during muxing."
        fi
    fi

    # Detect pip
    local pip_cmd
    pip_cmd="pip3"
    check_cmd "$pip_cmd" || pip_cmd="pip"

    # Repo
    local repo
    if is_in_repo; then
        repo="$PWD"
        msgbox "Repository" "Found OpenGrab repo at:\n$repo"
    else
        repo="$PWD/opengrab"
        infobox "Cloning" "Cloning OpenGrab repository..."
        if ! clone_repo "$repo"; then
            msgbox "Error" "Failed to clone repository."
            return 1
        fi
    fi

    # Venv
    if yesno "Virtual Environment" "Create a Python virtual environment?\n\nRecommended for clean isolation." "yes"; then
        infobox "Virtual Environment" "Creating virtual environment..."
        python3 -m venv "$repo/.venv"
        pip_cmd="$repo/.venv/bin/pip"
    fi

    # Install
    infobox "Installing" "Installing OpenGrab (pip install -e .)..."
    if ! run_with_gauge "Installing dependencies..." "pip install" \
        "$pip_cmd" install -e "$repo" 2>&1; then
        msgbox "Error" "pip install failed.\nCheck the output above."
        return 1
    fi

    # Config
    configure_env "$repo/.env"
    local port token
    port=$(cat "$TMP_OUT.port")
    token=$(cat "$TMP_OUT.token")

    # Systemd
    if yesno "Systemd Service" "Install as a systemd service?\n\nAuto-start on boot, restart on failure." "no"; then
        local python_exe
        if [ -x "$repo/.venv/bin/python" ]; then
            python_exe="$repo/.venv/bin/python"
        else
            python_exe="python3"
        fi
        local unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
        mkdir -p "$unit_dir"
        local env_vars=""
        while IFS='=' read -r key val; do
            case "$key" in
                OPENGRAB_TOKEN|OPENGRAB_PORT|OPENGRAB_DIR|OPENGRAB_MAX_JOBS|OPENGRAB_MAX_TOTAL_MB|OPENGRAB_AUTOUPDATE|OPENGRAB_MAX_SIZE_MB)
                    env_vars="$env_vars
Environment=$key=$val"
                    ;;
            esac
        done < "$repo/.env"
        cat > "$unit_dir/opengrab.service" <<EOF
[Unit]
Description=OpenGrab — self-hosted video downloader
After=network.target

[Service]
Type=simple
WorkingDirectory=$repo
ExecStart=$python_exe app.py
Restart=on-failure
RestartSec=10$env_vars

[Install]
WantedBy=multi-user.target
EOF
        msgbox "Systemd" "Unit written to:\n$unit_dir/opengrab.service\n\nTo enable and start:\n  systemctl --user enable --now opengrab.service\n\nFor lingering (survive logout):\n  loginctl enable-linger"
    fi

    # Success
    local python_exe
    if [ -x "$repo/.venv/bin/python" ]; then
        python_exe="$repo/.venv/bin/python"
    else
        python_exe="python3"
    fi
    msgbox "Installation Complete" \
        "OpenGrab is ready!\n\nStart it with:\n  cd $repo && $python_exe app.py\n\nURL:   http://localhost:$port\nToken: $token"
}

# ── Mode 3: Desktop Binary ───────────────────────────────────────────────────

mode_desktop() {
    local errs=""
    check_python_version || errs="$errs\n  ✗ python3 >= 3.12 (required)"
    check_cmd pip3 && true || check_cmd pip || errs="$errs\n  ✗ pip (required)"

    if [ -n "$errs" ]; then
        msgbox "Prerequisites" "Missing requirements:$errs\n\nInstall them and retry."
        return 1
    fi

    local pip_cmd
    pip_cmd="pip3"
    check_cmd "$pip_cmd" || pip_cmd="pip"

    # Repo
    local repo
    if is_in_repo; then
        repo="$PWD"
    else
        if ! check_cmd git; then
            msgbox "Error" "Git is required to clone the repository."
            return 1
        fi
        repo="$PWD/opengrab"
        infobox "Cloning" "Cloning OpenGrab repository..."
        if ! clone_repo "$repo"; then
            msgbox "Error" "Failed to clone repository."
            return 1
        fi
    fi

    # FFmpeg
    if [ ! -f "$repo/vendor/ffmpeg" ]; then
        if check_cmd ffmpeg; then
            if yesno "ffmpeg" "Copy system ffmpeg to vendor/?" "yes"; then
                mkdir -p "$repo/vendor"
                cp "$(command -v ffmpeg)" "$repo/vendor/ffmpeg"
            fi
        else
            msgbox "Warning" "ffmpeg not found in vendor/.\nThe desktop build bundles ffmpeg.\nWithout it, video processing will fail."
        fi
    fi

    # Install build deps
    infobox "Installing" "Installing build dependencies (PyInstaller + pywebview)..."
    if ! run_with_gauge "Installing build deps..." "pip install" \
        "$pip_cmd" install -e "${repo}[desktop,build]" 2>&1; then
        msgbox "Error" "pip install failed."
        return 1
    fi

    # PyInstaller build
    infobox "Building" "Building with PyInstaller...\nThis may take several minutes."
    if ! run_with_gauge "Running PyInstaller..." "PyInstaller" \
        python3 -m PyInstaller "$repo/OpenGrab.spec" --noconfirm 2>&1; then
        msgbox "Error" "PyInstaller build failed.\nCheck the output for details."
        return 1
    fi

    msgbox "Build Complete" "Output directory:\n$repo/dist/OpenGrab\n\nRun the binary from there.\n\nPre-built AppImage and macOS releases:\nhttps://github.com/Skydope/OpenGrab/releases"
}

# ── Main ─────────────────────────────────────────────────────────────────────

main() {
    # Welcome
    if [ "$DIALOG" = "dialog" ]; then
        dialog --title "OpenGrab Installer" \
            --msgbox "Welcome to the OpenGrab installer!\n\nSelf-hosted video & audio downloader.\nWraps yt-dlp (1800+ sites) + ffmpeg." 12 60
    else
        whiptail --title "OpenGrab Installer" \
            --msgbox "Welcome to the OpenGrab installer!\n\nSelf-hosted video & audio downloader.\nWraps yt-dlp (1800+ sites) + ffmpeg." 12 60
    fi

    # Mode selection
    menu_dlg "Installation Mode" "Choose how to install OpenGrab:" \
        "docker"    "Docker Compose (recommended — build + run container)" \
        "baremetal" "Bare Metal (pip install + systemd optional)" \
        "desktop"   "Desktop Binary (PyInstaller + AppImage optional)"

    local mode
    mode=$(cat "$TMP_OUT" 2>/dev/null || echo "")
    if [ -z "$mode" ]; then
        clear
        echo "Exited."
        exit 0
    fi

    clear
    case "$mode" in
        docker)    mode_docker ;;
        baremetal) mode_baremetal ;;
        desktop)   mode_desktop ;;
        *)         echo "Unknown mode: $mode"; exit 1 ;;
    esac

    clear
}

main "$@"
