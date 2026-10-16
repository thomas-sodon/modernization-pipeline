#!/bin/bash
# client-settings.sh <id>: print the client-session settings JSON (docs/contracts.md#client-session-settings;
# decisions L3.3). Needs only bash, jq and git, so it also runs piped:
#   git show origin/main:scripts/client-settings.sh | bash -s -- <id>
# The runbook writes the output to $HOME/engagements/<id>/client-session.json (chmod 600) and starts
# the session with `claude --settings` and that file. Every rule path is absolute, expanded from
# $HOME and written as // plus the path without its leading slash; directories end in /**.
set -euo pipefail

die() {
  echo "client-settings.sh: $1" >&2
  exit 2
}

[ $# -eq 1 ] || die "usage: client-settings.sh <engagement id>"
id=$1
[[ "$id" =~ ^[a-z][a-z0-9]*(-[a-z0-9]+)*$ ]] && [ ${#id} -le 40 ] || die "id must match ^[a-z][a-z0-9]*(-[a-z0-9]+)*$ (at most 40 characters)"
command -v jq >/dev/null 2>&1 || die "jq is missing"
[ -n "${HOME:-}" ] && [[ "$HOME" == /* ]] || die "HOME must be an absolute path"
home=${HOME%/}
[[ "$home" != *[*?[]* ]] || die "HOME holds a wildcard character"
eng_dir="$home/engagements/$id"
[ -d "$eng_dir" ] || die "$eng_dir does not exist"

rules=()
rule() { # tool path
  local p=$2
  [[ "$p" != *[*?[]* ]] || die "a path holds a wildcard character"
  rules+=("$1(/$p)")
}
dir_rule() { # tool dir
  local p=$2
  [[ "$p" != *[*?[]* ]] || die "a path holds a wildcard character"
  rules+=("$1(/$p/**)")
}

# this engagement's data zone
for d in sources datasets workbook reference goldens corpus results logs assets; do
  for t in Read Edit Write; do dir_rule "$t" "$eng_dir/private/$d"; done
done
for t in Read Edit Write; do rule "$t" "$eng_dir/private/scrub-map.json"; done

# every other engagement's private/ and record/
for d in "$home"/engagements/*/; do
  d=${d%/}
  [ -d "$d" ] || continue
  [ "$(basename "$d")" = "$id" ] && continue
  for sub in private record; do
    for t in Read Edit Write; do dir_rule "$t" "$d/$sub"; done
  done
done

# data.env
for t in Read Edit Write; do rule "$t" "$home/.config/modernization/data.env"; done

# installed tools, settings and this file: no Edit or Write
for t in Edit Write; do
  dir_rule "$t" "$home/.local/bin"
  dir_rule "$t" "$home/.local/share/modernization"
  dir_rule "$t" "$home/.config/modernization"
  dir_rule "$t" "$home/.claude"
  rule "$t" "$home/.claude.json"
  rule "$t" "$eng_dir/client-session.json"
done

# the files mp-run binds read-only: the worktree's Claude settings and the repos' git hooks and config
pl="$eng_dir/record/paths.local.json"
if [ -f "$pl" ]; then
  pipeline=$(jq -r '.pipeline // empty' "$pl")
  target_repo=$(jq -r '.target_repo // empty' "$pl")
  if [ -n "$pipeline" ]; then
    for t in Edit Write; do
      dir_rule "$t" "${pipeline%/}/.claude"
      rule "$t" "${pipeline%/}/.mcp.json"
    done
  fi
  for repo in "$pipeline" "$target_repo"; do
    [ -n "$repo" ] && [ -d "$repo" ] || continue
    common=$(git -C "$repo" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || continue
    for t in Edit Write; do
      dir_rule "$t" "$common/hooks"
      rule "$t" "$common/config"
    done
  done
fi

rules+=("Read(//proc/**)" "Monitor")

printf '%s\n' "${rules[@]}" | jq -R . | jq -s --arg hook "$home/.local/bin/mp-bash-hook" '{
  disableAllHooks: false,
  hooks: {PreToolUse: [{matcher: "Bash", hooks: [{type: "command", command: $hook}]}]},
  permissions: {
    disableBypassPermissionsMode: "disable",
    allow: ["Bash(mp-run:*)", "Bash(mp-data:*)"],
    deny: .
  },
  sandbox: {enabled: false}
}'
