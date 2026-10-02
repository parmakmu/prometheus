#!/usr/bin/env bash
# Create a user's folders for the web app, or update their copy of prometheus.
#
#   USERS_ROOT=/data/users ui/add-user.sh <user>            create
#   USERS_ROOT=/data/users ui/add-user.sh <user> --update   pull this repository's latest commit into their copy
#
# <user> is the name the login proxy sends (letters, digits, . _ - ; other characters become _).
# Creates USERS_ROOT/<user>/{prometheus (a clone of this repository, its .venv), pi-agent, files, home}.
# pi-agent gets your Pi settings and model key: every user's agents can read that key (v1, a team you know).
set -euo pipefail
user="$(printf '%s' "${1:?usage: add-user.sh <user> [--update]}" | sed 's/[^a-zA-Z0-9._-]/_/g')"
root="${USERS_ROOT:?set USERS_ROOT}"
repo="$(cd "$(dirname "$0")/.." && pwd)"
d="$root/$user"

if [[ "${2:-}" == "--update" ]]; then
  git -C "$d/prometheus" pull -q --ff-only
  (cd "$d/prometheus" && uv sync -q)
  echo "updated: $d/prometheus ($(git -C "$d/prometheus" log --oneline -1))"
  exit
fi

[[ -e "$d" ]] && { echo "$d exists (use --update to update their prometheus)" >&2; exit 1; }
mkdir -p "$d"/{home,files,pi-agent}
git clone -q "$repo" "$d/prometheus"
(cd "$d/prometheus" && uv sync -q)
cp "$HOME/.pi/agent/settings.json" "$d/pi-agent/"
[[ -f "$HOME/.pi/agent/auth.json" ]] && install -m 600 "$HOME/.pi/agent/auth.json" "$d/pi-agent/"
chmod 700 "$d"
echo "ready: $d  (put their input files in $d/files)"
