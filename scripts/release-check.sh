#!/bin/bash
# release-check.sh — Run before pushing to the public repo
# Catches personal info, internal references, secrets, and legacy branding
#
# Usage: bash scripts/release-check.sh

set -euo pipefail

RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
NC='\033[0m'

ERRORS=0
WARNINGS=0

check_pattern() {
    local label="$1"
    local pattern="$2"
    local severity="${3:-error}"  # error or warn
    
    local matches
    matches=$(grep -rn "$pattern" \
        --include="*.py" --include="*.md" --include="*.html" --include="*.json" \
        --include="*.yml" --include="*.yaml" --include="*.toml" --include="*.sh" \
        --include="*.ts" --include="*.js" --include="*.css" --include="*.txt" \
        --include="*.cfg" --include="*.service" --include="*.env*" \
        . 2>/dev/null | grep -v ".git/" | grep -v "__pycache__" | grep -v ".venv/" | \
        grep -v "node_modules/" | grep -v "scripts/release-check.sh" || true)
    
    if [ -n "$matches" ]; then
        if [ "$severity" = "error" ]; then
            echo -e "${RED}FAIL${NC} $label"
            ERRORS=$((ERRORS + 1))
        else
            echo -e "${YELLOW}WARN${NC} $label"
            WARNINGS=$((WARNINGS + 1))
        fi
        echo "$matches" | head -5 | sed 's/^/  /'
        local count
        count=$(echo "$matches" | wc -l | tr -d ' ')
        if [ "$count" -gt 5 ]; then
            echo "  ... and $((count - 5)) more"
        fi
        echo ""
    fi
}

check_file_exists() {
    local label="$1"
    local filepath="$2"
    
    if [ -f "$filepath" ] || git ls-files --error-unmatch "$filepath" &>/dev/null; then
        echo -e "${RED}FAIL${NC} $label: $filepath exists"
        ERRORS=$((ERRORS + 1))
        echo ""
    fi
}

check_file_size() {
    local max_kb="$1"
    local large_files
    large_files=$(find . -not -path "./.git/*" -not -path "./.venv/*" -not -path "./node_modules/*" \
        -type f -size "+${max_kb}k" 2>/dev/null | grep -v ".png$\|.jpg$\|.ico$\|.woff" || true)
    
    if [ -n "$large_files" ]; then
        echo -e "${YELLOW}WARN${NC} Files over ${max_kb}KB (check for accidental commits)"
        echo "$large_files" | sed 's/^/  /'
        WARNINGS=$((WARNINGS + 1))
        echo ""
    fi
}

echo "========================================="
echo "  Delega Release Check"
echo "========================================="
echo ""

# --- Personal / Infrastructure ---
echo "--- Personal & Infrastructure ---"
check_pattern "IP addresses (192.168.*)" "192\.168\." error
check_pattern "IP addresses (172.20.*)" "172\.20\." error
check_pattern "Tailscale/internal hostnames" "openclaw01\|caddy01\|fortigate" error
check_pattern "Personal domain" "mcmillan\.io\|home\.mcmillan" error
check_pattern "Personal names" "McMillan\|Jamie" error
check_pattern "Internal paths" "/Users/openclaw\|/etc/clawdbot\|/opt/homebrew" error
check_pattern "Internal hostnames" "delega\.home\.\|cfactory\.home\.\|dash\.home\." error

# --- Agent Names (internal team) ---
echo "--- Internal Agent References ---"
check_pattern "Agent: Biff (not in example code)" "\bBiff\b" error
check_pattern "Agent: Clara" "\bClara\b" error
check_pattern "Agent: Goldie" "\bGoldie\b" error
check_pattern "Agent: Needles" "\bNeedles\b" error
check_pattern "Agent: Strickland" "\bStrickland\b" error
check_pattern "Agent: Einstein" "\bEinstein\b" error
check_pattern "Agent: Jennifer" "\bJennifer\b" error
check_pattern "Agent: Lorraine" "\bLorraine\b" error
check_pattern "Agent: George" "\bGeorge\b" error
check_pattern "Agent: Doc Brown" "Doc Brown" error
check_pattern "Agent: Marty McFly" "Marty McFly" error
check_pattern "Agent: marty-clawed-bot" "marty-clawed-bot" error

# --- Secrets & Credentials ---
echo "--- Secrets & Credentials ---"
check_pattern "API key patterns (dlg_ actual keys)" "dlg_[a-zA-Z0-9_-]{20,}" error
check_pattern "Google API keys" "AIzaSy[a-zA-Z0-9_-]{30,}" error
check_pattern "OpenAI/generic sk- keys" "sk-[a-zA-Z0-9]{20,}" error
check_pattern "GitHub tokens" "ghp_[a-zA-Z0-9]{20,}\|github_pat_" error
check_pattern "Generic bearer tokens" "Bearer [a-zA-Z0-9_-]{20,}" warn

# --- Legacy Branding ---
echo "--- Legacy Branding ---"
check_pattern "TaskPine references" "[Tt]ask[Pp]ine" error
check_pattern "Flux as product name" "\"Flux\"\|Flux Task\|Flux API\|Flux Capacitor" error
check_pattern "BTTF references" "Back to the Future\|DeLorean\|1\.21 gigawatt\|Hill Valley\|BTTF" error
check_pattern "Old org references" "delegadev/\|twinpines/delega" error

# --- Blocked Files ---
echo "--- Blocked Files ---"
check_file_exists "Legacy: todoist_sync.py" "backend/todoist_sync.py"
check_file_exists "Legacy: fly.toml" "fly.toml"
check_file_exists "Legacy: old icons" "frontend/assets/icon-192-old.png"
check_file_exists "Legacy: old icons" "frontend/assets/icon-512-old.png"
check_file_exists "Secrets: .env file" ".env"
check_file_exists "Secrets: vapid_keys.json" "backend/vapid_keys.json"
check_file_exists "Secrets: vapid_keys.json" "vapid_keys.json"

# --- Code Quality ---
echo "--- Code Quality ---"
check_pattern "Internal task numbers" "Task #[0-9]\|task #[0-9]\|Delega Task #\|Flux Task #" warn
check_pattern "Debug prints" "print(f\"\|print(\"DEBUG\|breakpoint()\|import pdb" warn
check_file_size 500

# --- Summary ---
echo "========================================="
if [ "$ERRORS" -gt 0 ]; then
    echo -e "${RED}BLOCKED: $ERRORS error(s), $WARNINGS warning(s)${NC}"
    echo "Fix all errors before pushing to public."
    exit 1
elif [ "$WARNINGS" -gt 0 ]; then
    echo -e "${YELLOW}PASS with $WARNINGS warning(s)${NC}"
    echo "Review warnings, then push if acceptable."
    exit 0
else
    echo -e "${GREEN}CLEAN: No issues found${NC}"
    exit 0
fi
