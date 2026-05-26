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
MV_BIN="/bin/mv"
RM_BIN="/bin/rm"
MKDIR_BIN="/bin/mkdir"
CHMOD_BIN="/bin/chmod"

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

# ── temp file cleanup ─────────────────────────────────────────────────────────
PLUGIN_DEST_TMP=""
cleanup() {
  if [[ -n "$PLUGIN_DEST_TMP" && -f "$PLUGIN_DEST_TMP" ]]; then
    "$RM_BIN" -f "$PLUGIN_DEST_TMP"
  fi
}
trap cleanup EXIT INT TERM

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
if (( PY_VER < 311 )); then
  error "Python 3.11+ required (found $("$PYTHON_BIN" --version)). Upgrade with: brew upgrade python"
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
printf "  This installer will copy and make the local plugin executable.\n"
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

# ── patch plugin with real identity ───────────────────────────────────────────
heading "Configuring plugin"

PLUGIN_DEST_TMP=$("$MKTEMP_BIN" /tmp/claude-usage.XXXXXX.py)
"$CP_BIN" "$PLUGIN_SRC" "$PLUGIN_DEST_TMP"

if [[ -n "$VALID_GIT_USER" ]]; then
  REPO_URL="https://github.com/${VALID_GIT_USER}/claude-swiftbar"

  # sed uses | as delimiter; username is validated to [a-zA-Z0-9-] so safe.
  # macOS sed requires '' after -i; GNU sed does not — try macOS form first.
  if sed -i '' \
       -e "s|yourusername|${VALID_GIT_USER}|g" \
       -e "s|https://github.com/yourusername/claude-swiftbar|${REPO_URL}|g" \
       "$PLUGIN_DEST_TMP" 2>/dev/null; then
    : # macOS sed succeeded
  else
    sed -i \
      -e "s|yourusername|${VALID_GIT_USER}|g" \
      -e "s|https://github.com/yourusername/claude-swiftbar|${REPO_URL}|g" \
      "$PLUGIN_DEST_TMP"
  fi
  info "Repo links set to $REPO_URL"
else
  # Remove only the specific lines that print the placeholder repo URL,
  # leaving all other lines intact.
  PATCHED=$("$MKTEMP_BIN" /tmp/claude-usage.XXXXXX.py)
  grep -v 'yourusername/claude-swiftbar' "$PLUGIN_DEST_TMP" > "$PATCHED" || true
  "$MV_BIN" "$PATCHED" "$PLUGIN_DEST_TMP"
  info "Repo links omitted (no valid github.user configured)"
fi

# ── Keychain check ────────────────────────────────────────────────────────────
heading "Verifying Claude Code credentials"

# Only checks presence of the Keychain item — does not read or print contents.
if "$SECURITY_BIN" find-generic-password -s "$KEYCHAIN_SERVICE" -l &>/dev/null; then
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

# ── install plugin ────────────────────────────────────────────────────────────
heading "Installing plugin"

DEST="$PLUGINS_DIR/$PLUGIN_FILENAME"

# refuse to overwrite a symlink
if [[ -L "$DEST" ]]; then
  error "Destination is a symlink: $DEST"
  error "Remove or replace the symlink manually before installing."; exit 1
fi

# atomic install: temp file inside destination dir → rename into place
INSTALL_TMP=$("$MKTEMP_BIN" "$PLUGINS_DIR/.claude-usage-tmp.XXXXXX")
"$CP_BIN" "$PLUGIN_DEST_TMP" "$INSTALL_TMP"
"$CHMOD_BIN" +x "$INSTALL_TMP"     # set executable before it becomes visible
"$MV_BIN" "$INSTALL_TMP" "$DEST"   # atomic rename

info "Plugin installed: $DEST"

SRC_HASH=$("$SHASUM_BIN" -a 256 "$PLUGIN_SRC" | "$AWK_BIN" '{print $1}')
DEST_HASH=$("$SHASUM_BIN" -a 256 "$DEST"       | "$AWK_BIN" '{print $1}')
printf "\n"
printf "  Source SHA-256   : %s\n" "$SRC_HASH"
printf "  Installed SHA-256: %s\n" "$DEST_HASH"
printf "  Compare these against the release notes to verify the file.\n"

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
printf "\n"
