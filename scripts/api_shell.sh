#!/usr/bin/env bash
#
# Fuel Finder API Shell
# Interactive script for authenticating with the Fuel Finder API
# and running example queries. Requires curl and jq.
#
# Usage:
#   ./scripts/api_shell.sh                          # prompts for base URL
#   FF_BASE_URL=https://example.com ./scripts/api_shell.sh
#
set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'

# ── Dependencies ─────────────────────────────────────────────────────────
missing=()
for cmd in curl jq; do
    command -v "$cmd" >/dev/null || missing+=("$cmd")
done
if (( ${#missing[@]} > 0 )); then
    echo -e "\n${RED}Missing required tool(s): ${BOLD}${missing[*]}${RESET}\n"
    echo -e "This script needs ${BOLD}curl${RESET} (to make API requests) and ${BOLD}jq${RESET} (to parse JSON responses)."
    echo -e "\nInstall them with:"
    echo -e "  ${CYAN}macOS:${RESET}   brew install curl jq"
    echo -e "  ${CYAN}Ubuntu:${RESET}  sudo apt-get install -y curl jq"
    echo -e "  ${CYAN}Amazon Linux:${RESET} sudo yum install -y curl jq\n"
    exit 1
fi

# ── Configuration ────────────────────────────────────────────────────────
FF_BASE_URL="${FF_BASE_URL:-}"
FF_ID_TOKEN=""
FF_REFRESH_TOKEN=""
COGNITO_REGION=""
COGNITO_CLIENT_ID=""
TOKEN_EXPIRY=0
TOKEN_CACHE="${HOME}/.fuel-finder-tokens"

# ── Helpers ──────────────────────────────────────────────────────────────
hr()   { echo -e "${DIM}$(printf '─%.0s' {1..60})${RESET}"; }
info() { echo -e "${CYAN}ℹ ${RESET}$1"; }
ok()   { echo -e "${GREEN}✓ ${RESET}$1"; }
warn() { echo -e "${YELLOW}⚠ ${RESET}$1"; }
err()  { echo -e "${RED}✗ ${RESET}$1"; }

prompt_base_url() {
    if [[ -z "$FF_BASE_URL" ]]; then
        echo -e "${BOLD}Enter the Fuel Finder base URL${RESET} (e.g. https://fuel.hoy.la):"
        read -rp "> " FF_BASE_URL
    fi
    # Strip trailing slash
    FF_BASE_URL="${FF_BASE_URL%/}"
    # Prepend https:// if no scheme provided
    if [[ "$FF_BASE_URL" != http://* && "$FF_BASE_URL" != https://* ]]; then
        FF_BASE_URL="https://${FF_BASE_URL}"
    fi
    info "Base URL: ${BOLD}${FF_BASE_URL}${RESET}"
}

# ── Cognito helpers ──────────────────────────────────────────────────────
cognito_call() {
    local action="$1" body="$2"
    curl -sf "https://cognito-idp.${COGNITO_REGION}.amazonaws.com/" \
        -H "Content-Type: application/x-amz-json-1.1" \
        -H "X-Amz-Target: AWSCognitoIdentityProviderService.${action}" \
        -d "$body"
}

fetch_auth_config() {
    local resp
    resp=$(curl -sf "${FF_BASE_URL}/auth/config") || { err "Could not reach ${FF_BASE_URL}/auth/config"; return 1; }
    local mode
    mode=$(echo "$resp" | jq -r '.mode')

    if [[ "$mode" == "none" ]]; then
        ok "No-auth mode — no login required."
        FF_ID_TOKEN="__none__"
        return 0
    elif [[ "$mode" == "api_key" ]]; then
        warn "Server expects an API key."
        echo -e "Enter your API key:"
        read -rsp "> " api_key; echo
        FF_ID_TOKEN="__apikey__:${api_key}"
        return 0
    elif [[ "$mode" == "cognito" ]]; then
        COGNITO_REGION=$(echo "$resp" | jq -r '.region')
        COGNITO_CLIENT_ID=$(echo "$resp" | jq -r '.clientId')
        info "Cognito auth (region: ${COGNITO_REGION})"
        return 0
    else
        err "Unknown auth mode: ${mode}"; return 1
    fi
}

auth_header() {
    if [[ "$FF_ID_TOKEN" == "__none__" ]]; then
        echo ""
    elif [[ "$FF_ID_TOKEN" == __apikey__:* ]]; then
        echo "X-Api-Key: ${FF_ID_TOKEN#__apikey__:}"
    else
        echo "Authorization: Bearer ${FF_ID_TOKEN}"
    fi
}

api_get() {
    local path="$1"
    ensure_valid_token
    local hdr
    hdr=$(auth_header)
    if [[ -n "$hdr" ]]; then
        curl -sf "${FF_BASE_URL}/api/prices${path}" -H "$hdr"
    else
        curl -sf "${FF_BASE_URL}/api/prices${path}"
    fi
}

api_post() {
    local path="$1" body="$2"
    ensure_valid_token
    local hdr
    hdr=$(auth_header)
    if [[ -n "$hdr" ]]; then
        curl -sf "${FF_BASE_URL}/api${path}" -H "$hdr" -H "Content-Type: application/json" -d "$body"
    else
        curl -sf "${FF_BASE_URL}/api${path}" -H "Content-Type: application/json" -d "$body"
    fi
}

# ── Token management ─────────────────────────────────────────────────────
do_login() {
    echo -e "\n${BOLD}Sign in to Fuel Finder${RESET}"
    hr
    read -rp "Email / username: " username
    read -rsp "Password: " password; echo

    local body resp
    body=$(jq -n --arg cid "$COGNITO_CLIENT_ID" --arg u "$username" --arg p "$password" \
        '{AuthFlow:"USER_PASSWORD_AUTH",ClientId:$cid,AuthParameters:{USERNAME:$u,PASSWORD:$p}}')

    resp=$(cognito_call "InitiateAuth" "$body") || { err "Login failed. Check credentials."; return 1; }

    # Handle NEW_PASSWORD_REQUIRED challenge
    local challenge
    challenge=$(echo "$resp" | jq -r '.ChallengeName // empty')
    if [[ "$challenge" == "NEW_PASSWORD_REQUIRED" ]]; then
        warn "You must set a new password."
        local session challenge_user
        session=$(echo "$resp" | jq -r '.Session')
        challenge_user=$(echo "$resp" | jq -r '.ChallengeParameters.USER_ID_FOR_SRP')
        read -rsp "New password: " new_pw; echo
        body=$(jq -n --arg cid "$COGNITO_CLIENT_ID" --arg s "$session" --arg u "$challenge_user" --arg p "$new_pw" \
            '{ChallengeName:"NEW_PASSWORD_REQUIRED",ClientId:$cid,Session:$s,ChallengeResponses:{USERNAME:$u,NEW_PASSWORD:$p}}')
        resp=$(cognito_call "RespondToAuthChallenge" "$body") || { err "Password change failed."; return 1; }
    fi

    store_tokens "$resp"
}

store_tokens() {
    local resp="$1"
    FF_ID_TOKEN=$(echo "$resp" | jq -r '.AuthenticationResult.IdToken')
    local refresh
    refresh=$(echo "$resp" | jq -r '.AuthenticationResult.RefreshToken // empty')
    [[ -n "$refresh" ]] && FF_REFRESH_TOKEN="$refresh"

    # Decode expiry from JWT payload (base64url → base64)
    local payload exp_raw
    payload=$(echo "$FF_ID_TOKEN" | cut -d. -f2 | tr '_-' '/+')
    # Pad to multiple of 4
    while (( ${#payload} % 4 != 0 )); do payload+="="; done
    exp_raw=$(echo "$payload" | base64 -d 2>/dev/null | jq -r '.exp')
    TOKEN_EXPIRY="${exp_raw:-0}"

    local email
    email=$(echo "$payload" | base64 -d 2>/dev/null | jq -r '.email // .["cognito:username"] // "unknown"')
    ok "Signed in as ${BOLD}${email}${RESET}"
    local remaining=$(( TOKEN_EXPIRY - $(date +%s) ))
    info "Token expires in $(( remaining / 60 )) minutes"

    save_token_cache
}

do_refresh() {
    if [[ -z "$FF_REFRESH_TOKEN" ]]; then
        warn "No refresh token available — signing in again."
        do_login
        return
    fi

    info "Refreshing token…"
    local body resp
    body=$(jq -n --arg cid "$COGNITO_CLIENT_ID" --arg rt "$FF_REFRESH_TOKEN" \
        '{AuthFlow:"REFRESH_TOKEN_AUTH",ClientId:$cid,AuthParameters:{REFRESH_TOKEN:$rt}}')

    resp=$(cognito_call "InitiateAuth" "$body")
    if [[ $? -ne 0 ]]; then
        warn "Refresh token expired — signing in again."
        do_login
        return
    fi
    store_tokens "$resp"
}

# ── Token cache ────────────────────────────────────────────────────────
save_token_cache() {
    jq -n \
        --arg id "$FF_ID_TOKEN" \
        --arg refresh "$FF_REFRESH_TOKEN" \
        --arg base "$FF_BASE_URL" \
        --argjson exp "$TOKEN_EXPIRY" \
        '{id_token:$id, refresh_token:$refresh, base_url:$base, expiry:$exp}' \
        > "$TOKEN_CACHE"
    chmod 600 "$TOKEN_CACHE"
}

load_token_cache() {
    [[ -f "$TOKEN_CACHE" ]] || return 1
    local cached_base cached_id cached_refresh cached_exp
    cached_base=$(jq -r '.base_url' "$TOKEN_CACHE" 2>/dev/null) || return 1
    cached_id=$(jq -r '.id_token' "$TOKEN_CACHE" 2>/dev/null) || return 1
    cached_refresh=$(jq -r '.refresh_token' "$TOKEN_CACHE" 2>/dev/null) || return 1
    cached_exp=$(jq -r '.expiry' "$TOKEN_CACHE" 2>/dev/null) || return 1

    # Only reuse if same base URL
    [[ "$cached_base" != "$FF_BASE_URL" ]] && return 1
    # Must have at least a refresh token
    [[ -z "$cached_refresh" || "$cached_refresh" == "null" ]] && return 1

    FF_REFRESH_TOKEN="$cached_refresh"
    local now
    now=$(date +%s)

    if [[ -n "$cached_id" && "$cached_id" != "null" ]] && (( cached_exp > now + 60 )); then
        # ID token still valid
        FF_ID_TOKEN="$cached_id"
        TOKEN_EXPIRY="$cached_exp"
        local remaining=$(( cached_exp - now ))
        ok "Restored session from cache (token valid for $(( remaining / 60 ))m $(( remaining % 60 ))s)"
        return 0
    fi

    # ID token expired but we have a refresh token — try refreshing
    info "Cached token expired — refreshing…"
    do_refresh
    return $?
}

clear_token_cache() {
    rm -f "$TOKEN_CACHE"
    ok "Token cache cleared."
}

ensure_valid_token() {
    [[ "$FF_ID_TOKEN" == "__none__" || "$FF_ID_TOKEN" == __apikey__:* ]] && return 0
    [[ -z "$FF_ID_TOKEN" ]] && { do_login; return; }

    local now
    now=$(date +%s)
    if (( now >= TOKEN_EXPIRY - 60 )); then
        warn "Token expired or expiring soon."
        do_refresh
    fi
}

token_status() {
    if [[ "$FF_ID_TOKEN" == "__none__" ]]; then
        ok "No-auth mode — no token needed"
    elif [[ "$FF_ID_TOKEN" == __apikey__:* ]]; then
        ok "Using API key"
    elif [[ -z "$FF_ID_TOKEN" ]]; then
        warn "Not signed in"
    else
        local now remaining
        now=$(date +%s)
        remaining=$(( TOKEN_EXPIRY - now ))
        if (( remaining > 0 )); then
            ok "Token valid for $(( remaining / 60 )) min $(( remaining % 60 ))s"
        else
            warn "Token expired $(( -remaining ))s ago — will auto-refresh on next request"
        fi
    fi
}

# ── Generate env vars helper ─────────────────────────────────────────────
show_env_export() {
    if [[ -z "$FF_ID_TOKEN" || "$FF_ID_TOKEN" == "__none__" ]]; then
        warn "No token to export."
        return
    fi
    echo -e "\n${BOLD}Copy and paste these into your terminal to set up your environment:${RESET}\n"
    if [[ "$FF_ID_TOKEN" == __apikey__:* ]]; then
        echo -e "  export FF_BASE_URL='${FF_BASE_URL}'"
        echo -e "  export FF_API_KEY='${FF_ID_TOKEN#__apikey__:}'"
        echo -e "\n  ${DIM}# Then use:  curl -H \"X-Api-Key: \$FF_API_KEY\" \"\$FF_BASE_URL/api/prices/by-region\"${RESET}"
    else
        echo -e "  export FF_BASE_URL='${FF_BASE_URL}'"
        echo -e "  export FF_ID_TOKEN='${FF_ID_TOKEN}'"
        echo -e "\n  ${DIM}# Then use:  curl -H \"Authorization: Bearer \$FF_ID_TOKEN\" \"\$FF_BASE_URL/api/prices/by-region\"${RESET}"
    fi
    echo
    hr
    echo -e "  ${CYAN}1${RESET}  Quit so you can paste these into your terminal"
    echo -e "  ${CYAN}2${RESET}  Return to main menu"
    hr
    read -rp "Choose [1/2]: " subchoice
    case "$subchoice" in
        1) echo -e "\n${DIM}Bye! Paste the export commands above into your terminal.${RESET}"; exit 0 ;;
        *) ;;
    esac
}

# ── Curl hint helper ─────────────────────────────────────────────────────
_curl_hint_header() {
    echo -e "\n${BOLD}To run this yourself:${RESET}"
    echo -e "  ${DIM}1.${RESET} Generate environment variables for your token (menu option ${CYAN}g${RESET})"
    echo -e "  ${DIM}2.${RESET} Copy and paste the export commands into your terminal"
    echo -e "  ${DIM}3.${RESET} Run this command:\n"
}

_curl_hint_auth_line() {
    if [[ "$FF_ID_TOKEN" == __apikey__:* ]]; then
        echo -e "    -H \"X-Api-Key: \$FF_API_KEY\" \\"
    elif [[ "$FF_ID_TOKEN" != "__none__" ]]; then
        echo -e "    -H \"Authorization: Bearer \$FF_ID_TOKEN\" \\"
    fi
}

_curl_hint_params() {
    local path="$1"
    local base="${path%%\?*}"
    local query="${path#*\?}"
    [[ "$query" == "$path" ]] && query=""

    # Output --data-urlencode lines for each param, then the URL
    if [[ -n "$query" ]]; then
        echo -e "    -G \\"
        local IFS='&'
        local params=($query)
        for param in "${params[@]}"; do
            echo -e "    --data-urlencode \"${param}\" \\"
        done
    fi
    echo -e "    \"${FF_BASE_URL}/api/prices${base}\""
}

curl_hint_get() {
    local path="$1"
    _curl_hint_header
    echo -e "  curl \\"
    _curl_hint_auth_line
    _curl_hint_params "$path"
    echo
}

curl_hint_post() {
    local path="$1" body="$2"
    _curl_hint_header
    echo -e "  curl \\"
    _curl_hint_auth_line
    echo -e "    -H 'Content-Type: application/json' \\"
    echo -e "    -d '${body}' \\"
    echo -e "    \"${FF_BASE_URL}/api${path}\""
    echo
}

# ── Example queries ──────────────────────────────────────────────────────
example_regional_avg() {
    echo -e "\n${BOLD}Average E10 price across Northern England regions${RESET}"
    hr
    local regions="North West,North East,Yorkshire and The Humber"
    info "Regions: ${regions}"
    info "Endpoint: GET /api/prices/by-region?fuel_type=E10\n"

    local resp
    resp=$(api_get "/by-region?fuel_type=E10") || { err "Request failed."; return; }

    echo "$resp" | jq --arg regions "$regions" '
        [.[] | select(.region as $r | ($regions | split(",") | any(. == $r)))]
        | sort_by(.region)
        | .[] | {region, avg_price: (.avg_price | tostring + "p"), stations: .station_count}
    '

    local overall
    overall=$(echo "$resp" | jq --arg regions "$regions" '
        [.[] | select(.region as $r | ($regions | split(",") | any(. == $r)))]
        | (map(.avg_price * .station_count) | add) / (map(.station_count) | add)
        | . * 10 | round / 10
    ')
    echo -e "\n${GREEN}Weighted average across all three regions: ${BOLD}${overall}p${RESET}"
    curl_hint_get "/by-region?fuel_type=E10"
    echo -e "${DIM}All endpoints are listed at ${FF_BASE_URL}/api${RESETq}"
}

example_brand_history() {
    echo -e "\n${BOLD}Price history for Welcome Break stations${RESET}"
    hr
    local days
    read -rp "How many days of history? [30]: " days
    days="${days:-30}"
    info "Endpoint: GET /api/prices/history?fuel_type=E10&brand=Welcome Break&days=${days}\n"

    local resp
    resp=$(api_get "/history?fuel_type=E10&brand=Welcome%20Break&days=${days}") || { err "Request failed."; return; }

    local granularity count
    granularity=$(echo "$resp" | jq -r '.granularity')
    count=$(echo "$resp" | jq '.data | length')
    info "Granularity: ${granularity}, ${count} data points\n"

    echo "$resp" | jq '.data | if length > 10 then
        (.[0:5] + [{"bucket":"…","avg_price":"…","stations":"…"}] + .[-5:])
    else . end | .[] | {date: .bucket, avg_price: (.avg_price | tostring + "p"), stations}'

    curl_hint_get "/history?fuel_type=E10&brand=Welcome Break&days=${days}"
    echo -e "${DIM}All endpoints are listed at ${FF_BASE_URL}/api${RESET}"
}

example_batch_history() {
    echo -e "\n${BOLD}Batch station lookup + price history${RESET}"
    hr
    echo -e "Enter node IDs separated by commas (or spaces):"
    read -rp "> " raw_ids
    # Normalise: strip spaces around commas, replace remaining spaces with commas
    local ids
    ids=$(echo "$raw_ids" | sed 's/ *, */,/g; s/ /,/g')
    local count
    count=$(echo "$ids" | tr ',' '\n' | wc -l | tr -d ' ')
    info "Looking up ${count} station(s)…\n"

    # Station metadata via POST
    local json_ids resp
    json_ids=$(echo "$ids" | tr ',' '\n' | jq -R . | jq -s '{node_ids: .}')
    resp=$(api_post "/stations/lookup" "$json_ids") || { err "Lookup failed."; return; }
    echo -e "${BOLD}Station details:${RESET}"
    echo "$resp" | jq '.results[] | {node_id, trading_name, brand, postcode, city, region}'

    local found
    found=$(echo "$resp" | jq -r '[.results[] | select(.found==true) | .node_id] | join(",")')
    if [[ -z "$found" ]]; then
        warn "No valid stations found — skipping history."
        return
    fi

    local days
    read -rp "Days of history to fetch? [30]: " days
    days="${days:-30}"
    info "Fetching E10 price history for matched stations…\n"

    local hist
    hist=$(api_get "/history?fuel_type=E10&node_ids=${found}&days=${days}") || { err "History request failed."; return; }
    echo "$hist" | jq '.data | if length > 10 then
        (.[0:5] + [{"bucket":"…","avg_price":"…","stations":"…"}] + .[-5:])
    else . end | .[] | {date: .bucket, avg_price: (.avg_price | tostring + "p"), stations}'

    curl_hint_post "/stations/lookup" "$json_ids"
    curl_hint_get "/history?fuel_type=E10&node_ids=${found}&days=${days}"
    echo -e "${DIM}All endpoints are listed at ${FF_BASE_URL}/api${RESET}"
}

# ── Main menu ────────────────────────────────────────────────────────────
show_menu() {
    echo -e "\n${BOLD}Fuel Finder API Shell${RESET}"
    hr
    echo -e "  ${DIM}TOOLS${RESET}"
    echo -e "  ${CYAN}t${RESET}  Token status"
    echo -e "  ${CYAN}r${RESET}  Refresh / re-authenticate"
    echo -e "  ${CYAN}g${RESET}  Generate environment variables for using your token in curl requests"
    echo -e "  ${CYAN}c${RESET}  Clear cached tokens"
    echo -e "  ${CYAN}q${RESET}  Quit"
    echo
    echo -e "  ${DIM}EXAMPLES${RESET}"
    echo -e "  ${CYAN}1${RESET}  Average E10 price across Northern England regions"
    echo -e "  ${CYAN}2${RESET}  Price history for Welcome Break stations"
    echo -e "  ${CYAN}3${RESET}  Batch node ID lookup + price history"
    hr
}

main() {
    echo -e "\n${BOLD}🔧 Fuel Finder API Shell${RESET}\n"

    prompt_base_url
    fetch_auth_config || exit 1

    # Cognito mode — try cached tokens, fall back to login
    if [[ "$FF_ID_TOKEN" != "__none__" && "$FF_ID_TOKEN" != __apikey__:* ]]; then
        if ! load_token_cache; then
            do_login || exit 1
        fi
    fi

    while true; do
        show_menu
        read -rp "Choose [1-3, t/r/g/c/q]: " choice
        case "$choice" in
            1) example_regional_avg ;;
            2) example_brand_history ;;
            3) example_batch_history ;;
            t) token_status ;;
            r) do_refresh ;;
            g) show_env_export ;;
            c) clear_token_cache ;;
            q) echo -e "\n${DIM}Bye!${RESET}"; exit 0 ;;
            *) warn "Invalid choice." ;;
        esac
    done
}

main
