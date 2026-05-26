#!/usr/bin/env bash
# install.sh — install the claude-swiftbar plugin into SwiftBar or xbar
#
# Security notes
# ──────────────
# • Downloads nothing. Installs only the local plugin file from this repo.
# • Does not read, print, copy, or modify Claude credentials.
# • Only checks whether the Keychain entry "Claude Code-credentials" exists.
# • Does not request sudo or modify shell profiles or system settings.
# • Does not install Homebrew, Python, SwiftBar, or xbar.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── safe PATH ─────────────────────────────────────────────────────────────────
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin"

# ── absolute paths for system tools ──────────────────────────────────────────
SECURITY_BIN="/usr/bin/security"
SHASUM_BIN="/usr/bin/shasum"
AWK_BIN="/usr/bin/awk"
PGREP_BIN="/usr/bin/pgrep"
OPEN_BIN="/usr/bin/open"
MKTEMP_BIN="/usr/bin/mktemp"
CP_BIN="/bin/cp"
RM_BIN="/bin/rm"
MKDIR_BIN="/bin/mkdir"
CHMOD_BIN="/bin/chmod"
LN_BIN="/bin/ln"
READLINK_BIN="/usr/bin/readlink"

# python3 may come from Homebrew — resolve via PATH
if ! PYTHON_BIN="$(command -v python3 2>/dev/null)"; then
  PYTHON_BIN=""
fi

# ── constants ─────────────────────────────────────────────────────────────────
PLUGIN_SRC="plugins/claude-usage.5m.py"
PLUGIN_FILENAME="claude-usage.5m.py"
KEYCHAIN_SERVICE="Claude Code-credentials"
SWIFTBAR_DIR="$HOME/Library/Application Support/SwiftBar/Plugins"
XBAR_DIR="$HOME/Library/Application Support/xbar/plugins"

# ── colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
YELLOW='\033[0;33m'
GREEN='\033[0;32m'
BOLD='\033[1m'
RESET='\033[0m'

info()    { printf "  ${GREEN}✔${RESET}  %s\n" "$*"; }
warn()    { printf "  ${YELLOW}⚠${RESET}  %s\n" "$*"; }
error()   { printf "  ${RED}✖${RESET}  %s\n" "$*" >&2; }
heading() { printf "\n${BOLD}%s${RESET}\n" "$*"; }

# ── preflight ─────────────────────────────────────────────────────────────────
heading "Checking prerequisites"

if [[ "$(uname)" != "Darwin" ]]; then
  error "This plugin requires macOS."; exit 1
fi
info "macOS $(sw_vers -productVersion)"

if [[ -z "$PYTHON_BIN" ]]; then
  error "python3 not found. Install with: brew install python"; exit 1
fi
PY_VER=$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}{sys.version_info.minor}')")
if (( PY_VER < 39 )); then
  error "Python 3.9+ required (found $("$PYTHON_BIN" --version)). Upgrade with: brew upgrade python"
  exit 1
fi
info "Python $("$PYTHON_BIN" --version)"

if [[ ! -f "$PLUGIN_SRC" ]]; then
  error "Plugin file not found: $PLUGIN_SRC"
  error "Run this script from the root of the cloned repository."; exit 1
fi
info "Plugin file found"

SRC_HASH=$("$SHASUM_BIN" -a 256 "$PLUGIN_SRC" | "$AWK_BIN" '{print $1}')
printf "  Source SHA-256 : %s\n" "$SRC_HASH"
printf "  This installer creates a symlink — git pull will update the running plugin.\n"
printf "  No remote code is downloaded.\n"

# ── resolve GitHub identity from git config ───────────────────────────────────
heading "Reading git identity"

GIT_USER=""
if command -v git &>/dev/null; then
  GIT_USER=$(git config --global github.user 2>/dev/null \
    || git config --global user.name 2>/dev/null \
    || true)
fi

# ── validate GitHub username ──────────────────────────────────────────────────
# Accept only: alphanumeric + hyphen, 1–39 chars,
# no leading/trailing hyphen, no consecutive hyphens.
VALID_GIT_USER=""
if [[ -n "$GIT_USER" ]]; then
  if [[ "$GIT_USER" =~ ^[a-zA-Z0-9]([a-zA-Z0-9-]{0,37}[a-zA-Z0-9])?$ ]] \
     && [[ ! "$GIT_USER" =~ -- ]]; then
    VALID_GIT_USER="$GIT_USER"
    info "GitHub user: $VALID_GIT_USER"
  else
    warn "git user '$GIT_USER' is not a valid GitHub username — repo links will be omitted"
    warn "Set a valid name with: git config --global github.user YOUR_GITHUB_USERNAME"
  fi
else
  warn "git github.user not set — repo links will be omitted from the plugin"
  warn "Set it with: git config --global github.user YOUR_GITHUB_USERNAME"
fi

# Check git email is configured (do not print the address)
if command -v git &>/dev/null && git config --global user.email &>/dev/null; then
  info "git email is configured"
else
  warn "git user.email not set — security contact will be omitted from SECURITY.md"
fi

# ── patch plugin source with real identity ────────────────────────────────────
heading "Configuring plugin"

# Patch is applied directly to the source file so the symlink picks it up.
# Only runs if the placeholder is still present (i.e. a fresh clone/fork).
if grep -q 'yourusername' "$PLUGIN_SRC" 2>/dev/null; then
  if [[ -n "$VALID_GIT_USER" ]]; then
    REPO_URL="https://github.com/${VALID_GIT_USER}/claude-swiftbar"
    PATCH_TMP=$("$MKTEMP_BIN" /tmp/claude-usage.XXXXXX.py)
    "$CP_BIN" "$PLUGIN_SRC" "$PATCH_TMP"
    # sed uses | as delimiter; username is validated to [a-zA-Z0-9-] so safe.
    # macOS sed requires '' after -i; GNU sed does not — try macOS form first.
    if sed -i '' \
         -e "s|yourusername|${VALID_GIT_USER}|g" \
         -e "s|https://github.com/yourusername/claude-swiftbar|${REPO_URL}|g" \
         "$PATCH_TMP" 2>/dev/null; then
      : # macOS sed succeeded
    else
      sed -i \
        -e "s|yourusername|${VALID_GIT_USER}|g" \
        -e "s|https://github.com/yourusername/claude-swiftbar|${REPO_URL}|g" \
        "$PATCH_TMP"
    fi
    "$CP_BIN" "$PATCH_TMP" "$PLUGIN_SRC"
    "$RM_BIN" -f "$PATCH_TMP"
    info "Repo links set to $REPO_URL"
  else
    warn "yourusername placeholder found but no valid github.user configured"
    warn "Set it with: git config --global github.user YOUR_GITHUB_USERNAME"
    warn "Then re-run install.sh to patch the source file."
  fi
else
  info "Plugin source already configured"
fi

# ── Keychain check ────────────────────────────────────────────────────────────
heading "Verifying Claude Code credentials"

# Only checks presence of the Keychain item — does not read or print contents.
if "$SECURITY_BIN" find-generic-password -s "$KEYCHAIN_SERVICE" &>/dev/null; then
  info "Keychain entry found: \"$KEYCHAIN_SERVICE\""
else
  warn "Keychain entry \"$KEYCHAIN_SERVICE\" not found"
  warn "Run: claude auth login"
fi

# ── detect plugins folder ─────────────────────────────────────────────────────
heading "Detecting menu-bar app"

PLUGINS_DIR=""
APP_NAME=""

if [[ -d "$SWIFTBAR_DIR" ]]; then
  PLUGINS_DIR="$SWIFTBAR_DIR"
  APP_NAME="SwiftBar"
elif [[ -d "$XBAR_DIR" ]]; then
  PLUGINS_DIR="$XBAR_DIR"
  APP_NAME="xbar"
else
  printf "\n"
  printf "  Neither SwiftBar nor xbar plugins folder detected.\n"
  printf "  Install SwiftBar first:  brew install swiftbar\n"
  printf "  Or enter an absolute path to your plugins folder (leave blank to abort): "
  read -r PLUGINS_DIR

  # reject blank
  if [[ -z "$PLUGINS_DIR" ]]; then
    error "Aborted — no plugins folder specified."; exit 1
  fi

  # require absolute path
  if [[ "$PLUGINS_DIR" != /* ]]; then
    error "Path must be absolute (start with /). Aborted."; exit 1
  fi

  # reject if the path exists but is not a directory
  if [[ -e "$PLUGINS_DIR" && ! -d "$PLUGINS_DIR" ]]; then
    error "Path exists but is not a directory: $PLUGINS_DIR"; exit 1
  fi

  APP_NAME="custom"

  # confirm before installing into an unrecognised directory
  printf "  Installing an executable plugin into: %s\n" "$PLUGINS_DIR"
  printf "  Proceed? [y/N] "
  read -r CONFIRM
  if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
    error "Aborted by user."; exit 1
  fi
fi

"$MKDIR_BIN" -p "$PLUGINS_DIR"
info "Using $APP_NAME plugins folder: $PLUGINS_DIR"

# ── install plugin (symlink) ──────────────────────────────────────────────────
heading "Installing plugin"

PLUGIN_ABS="$SCRIPT_DIR/$PLUGIN_SRC"
DEST="$PLUGINS_DIR/$PLUGIN_FILENAME"

if [[ -L "$DEST" ]]; then
  existing_target=$("$READLINK_BIN" "$DEST")
  if [[ "$existing_target" == "$PLUGIN_ABS" ]]; then
    info "Symlink already up to date — reinstalling"
  else
    warn "Replacing existing symlink (was: $existing_target)"
  fi
elif [[ -f "$DEST" ]]; then
  warn "Replacing existing plugin copy with a symlink."
  warn "After this, git pull in the repo updates the running plugin automatically."
  "$RM_BIN" -f "$DEST"
fi

"$CHMOD_BIN" +x "$PLUGIN_ABS"
"$LN_BIN" -sf "$PLUGIN_ABS" "$DEST"

info "Symlink installed:"
info "  $DEST -> $PLUGIN_ABS"

SRC_HASH=$("$SHASUM_BIN" -a 256 "$PLUGIN_ABS" | "$AWK_BIN" '{print $1}')
printf "\n"
printf "  Plugin SHA-256: %s\n" "$SRC_HASH"
printf "  To update: git pull inside the repository folder.\n"

# ── reload ────────────────────────────────────────────────────────────────────
heading "Reloading $APP_NAME"

if [[ "$APP_NAME" == "SwiftBar" ]] && "$PGREP_BIN" -q SwiftBar 2>/dev/null; then
  "$OPEN_BIN" -a SwiftBar
  info "SwiftBar reloaded"
else
  info "Open $APP_NAME to activate the plugin"
fi

printf "\n"
printf "${BOLD}${GREEN}Done!${RESET} Look for 🤖 in your menu bar.\n"
printf "  Refreshes every 5 minutes. Click 🤖 → Refresh to poll immediately.\n"
printf "  Update any time: git pull (in the repository folder).\n"
printf "\n"
