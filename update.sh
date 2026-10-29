#!/usr/bin/env bash
# Update InstaPost on the server to the latest code, in one go:
#
#   bash update.sh              fetch, back up the database, rebuild, restart, check health
#   bash update.sh --force      rebuild even if there is no new code
#   bash update.sh --public     (or --local / --proxy) also change how the dashboard is reached
#
# Your own edits to brand.yaml, schedule.yaml or data/ are kept. If the new code changes the
# same file, nothing is touched and you are told which file to sort out first.
# Everything is inside main(), so bash has read the whole script before git replaces it.
set -euo pipefail

main() {
  local app; app="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  cd "$app"
  say() { printf '\033[1m==> %s\033[0m\n' "$*"; }
  die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
  local git=(git -c safe.directory="$app")

  local force=0 mode=()
  for arg in "$@"; do
    case "$arg" in
      --force) force=1 ;;
      --public|--local|--proxy) mode=("$arg") ;;
      -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
      *) die "unknown option $arg (see: bash update.sh --help)" ;;
    esac
  done

  # Only one update at a time.
  exec 9>"$app/deploy/.update.lock"
  flock -n 9 || die "another update is already running"

  # --- 1. fetch ------------------------------------------------------------------------------
  local branch upstream
  branch="$("${git[@]}" rev-parse --abbrev-ref HEAD)"
  [ "$branch" != "HEAD" ] || die "not on a branch (detached HEAD); run: git checkout $branch"
  upstream="$("${git[@]}" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null \
              || echo "origin/$branch")"
  say "Fetching the latest code ($upstream)"
  local remote="${upstream%%/*}" rbranch="${upstream#*/}"
  "${git[@]}" fetch --quiet "$remote" "$rbranch"

  local old new
  old="$("${git[@]}" rev-parse HEAD)"
  new="$("${git[@]}" rev-parse "$upstream")"

  # The commit the running containers were built from (written after each good deploy). If
  # the code was pulled by hand ("git pull"), the containers are still old: rebuild them.
  local deployed=""
  [ -f deploy/.deployed ] && deployed="$(cat deploy/.deployed)"

  if [ "$old" = "$new" ]; then
    echo "Already up to date ($("${git[@]}" log -1 --format='%h %s' "$old"))."
    if [ "$deployed" != "$new" ]; then
      echo "The running app was not built from this code yet: rebuilding."
      force=1
    fi
    if [ "$force" = 0 ] && [ ${#mode[@]} = 0 ]; then
      bash deploy/docker.sh status
      exit 0
    fi
  else
    "${git[@]}" merge-base --is-ancestor "$old" "$new" \
      || die "this folder has commits that are not on $upstream; not updating automatically"
    echo "New changes:"
    "${git[@]}" log --format='  %h %s' "$old..$new"

    # --- 2. keep local edits ---------------------------------------------------------------
    local mine theirs clash
    mine="$("${git[@]}" diff --name-only HEAD)"
    theirs="$("${git[@]}" diff --name-only "$old" "$new")"
    clash="$(comm -12 <(sort <<<"$mine") <(sort <<<"$theirs") | sed '/^$/d')"
    if [ -n "$clash" ]; then
      echo "ERROR: you edited these files, and the new code changes them too:" >&2
      sed 's/^/  /' <<<"$clash" >&2
      echo "Nothing was changed. Save your copy, take the new version, re-apply your edit:" >&2
      echo "  cp <file> <file>.mine && git checkout -- <file> && bash update.sh" >&2
      exit 1
    fi
  fi

  # --- 3. back up the database -------------------------------------------------------------
  if [ -f state/instapost.db ]; then
    mkdir -p state/backups
    local backup; backup="state/backups/before-update-$(date +%Y%m%d-%H%M%S).db"
    say "Backing up the database to $backup"
    # SQLite's own backup is safe while the worker is writing; fall back to a copy.
    if ! docker exec instapost-worker-1 python3 -c \
        "import sqlite3; s=sqlite3.connect('/app/state/instapost.db'); \
d=sqlite3.connect('/app/$backup'); s.backup(d); d.close()" 2>/dev/null; then
      cp state/instapost.db "$backup"
    fi
    # Keep the 5 newest.
    ls -1t state/backups/before-update-*.db 2>/dev/null | tail -n +6 | xargs -r rm -f
  fi

  # --- 4. update the code ------------------------------------------------------------------
  if [ "$old" != "$new" ]; then
    say "Updating the code"
    local stashed=0
    if [ -n "$("${git[@]}" status --porcelain --untracked-files=no)" ]; then
      "${git[@]}" stash push --quiet -m "update.sh: local edits" && stashed=1
    fi
    "${git[@]}" merge --ff-only --quiet "$new"
    if [ "$stashed" = 1 ]; then
      "${git[@]}" stash pop --quiet || die "could not re-apply your edits; they are safe in \
'git stash list'"
    fi
  fi

  # --- 5. rebuild and restart (a fresh bash runs the new deploy script) ---------------------
  if ! bash deploy/docker.sh deploy "${mode[@]}"; then
    echo >&2
    echo "ERROR: the deploy failed. The previous containers keep running if the build failed." >&2
    if [ "$old" != "$new" ]; then
      echo "To go back to the version you had:" >&2
      echo "  git reset --keep ${old:0:12} && bash deploy/docker.sh deploy" >&2
    fi
    exit 1
  fi
  "${git[@]}" rev-parse HEAD > deploy/.deployed
  say "Updated to $("${git[@]}" log -1 --format='%h %s')"
}

main "$@"
exit 0
