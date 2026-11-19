#!/usr/bin/env bash
# Create .env from .env.example with strong random secrets.
#   scripts/generate-secrets.sh                 # create .env (refuses to overwrite)
#   scripts/generate-secrets.sh --rotate NAME   # rotate one secret in an existing .env
set -euo pipefail
cd "$(dirname "$0")/.."

random_password() {  # 24 chars, always satisfies the Keycloak password policy (upper, lower, digit)
  printf 'Dh1%s' "$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 21)"
}
random_hex32() { LC_ALL=C tr -dc 'a-f0-9' </dev/urandom | head -c 32; }

value_for() {
  case "$1" in
    OAUTH2_PROXY_COOKIE_SECRET) random_hex32 ;;  # oauth2-proxy needs exactly 16, 24 or 32 bytes
    *) random_password ;;
  esac
}

if [[ "${1:-}" == "--rotate" ]]; then
  name="${2:?usage: --rotate NAME}"
  [[ -f .env ]] || { echo ".env not found" >&2; exit 1; }
  grep -q "^${name}=" .env || { echo "${name} not found in .env" >&2; exit 1; }
  new="$(value_for "$name")"
  sed -i "s|^${name}=.*|${name}=${new}|" .env
  echo "Rotated ${name}. Apply with: make deploy ENV=<env>"
  exit 0
fi

if [[ -f .env ]]; then
  echo ".env already exists — refusing to overwrite (use --rotate NAME)." >&2
  exit 1
fi

umask 077
while IFS= read -r line; do
  if [[ "$line" =~ ^([A-Z0-9_]+)=CHANGE_ME ]]; then
    name="${BASH_REMATCH[1]}"
    echo "${name}=$(value_for "$name")"
  else
    echo "$line"
  fi
done < .env.example > .env
chmod 600 .env
echo "Created .env with $(grep -c '' .env) lines (permissions 600). Keep it out of Git and back it up securely."
