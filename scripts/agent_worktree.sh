#!/usr/bin/env bash
# Isolated worktrees for coding agents: create, check, list, remove.
#
# Never destructive: it refuses rather than forces. It never deletes a branch,
# never removes a worktree with uncommitted changes, and never touches the
# main worktree's checkout.
#
#   scripts/agent_worktree.sh create <task-slug> [--agent <name>] [--base <ref>]
#                                   (base default: origin/main, else main)
#   scripts/agent_worktree.sh check
#   scripts/agent_worktree.sh list
#   scripts/agent_worktree.sh remove <task-slug> [--agent <name>]
#
# Branch:   agent/<agent>/<task-slug>                    (deterministic)
# Location: $LOSSLIFT_WORKTREES/<agent>--<task-slug>
#           (default: <parent of the main worktree>/losslift-worktrees)
# Agent:    --agent, else $LOSSLIFT_AGENT, else "agent"
set -euo pipefail

die() { echo "agent_worktree: $*" >&2; exit 1; }

usage() {
  sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

SLUG_RE='^[a-z0-9][a-z0-9-]{0,47}$'
AGENT_RE='^[a-z0-9][a-z0-9-]{0,31}$'

git rev-parse --git-dir >/dev/null 2>&1 || die "not inside a git repository"
COMMON_DIR=$(git rev-parse --path-format=absolute --git-common-dir)
MAIN_ROOT=$(dirname "$COMMON_DIR")
WORKTREES=${LOSSLIFT_WORKTREES:-$(dirname "$MAIN_ROOT")/losslift-worktrees}

parse_names() {
  SLUG="${1:-}"
  shift || true
  AGENT="${LOSSLIFT_AGENT:-agent}"
  # origin/main, not a possibly stale local main, unless told otherwise.
  if git rev-parse --verify --quiet "origin/main^{commit}" >/dev/null; then BASE="origin/main"; else BASE="main"; fi
  while [ $# -gt 0 ]; do
    case "$1" in
      --agent) AGENT="${2:-}"; shift 2 ;;
      --base) BASE="${2:-}"; shift 2 ;;
      *) die "unknown option: $1" ;;
    esac
  done
  [[ "$SLUG" =~ $SLUG_RE ]] || die "task slug must match $SLUG_RE (lower-case, digits, hyphens)"
  [[ "$AGENT" =~ $AGENT_RE ]] || die "agent name must match $AGENT_RE"
  BRANCH="agent/$AGENT/$SLUG"
  DIR="$WORKTREES/$AGENT--$SLUG"
}

cmd_create() {
  parse_names "$@"
  local base_sha
  base_sha=$(git rev-parse --verify --quiet "$BASE^{commit}") || die "base '$BASE' is not a commit here (fetch it first)"
  git show-ref --verify --quiet "refs/heads/$BRANCH" && die "branch $BRANCH already exists; pick another task slug or remove it deliberately"
  [ -e "$DIR" ] && die "$DIR already exists"
  mkdir -p "$WORKTREES"
  git worktree add --quiet -b "$BRANCH" "$DIR" "$base_sha"
  git config "branch.$BRANCH.agentbase" "$base_sha"
  cat <<EOF
Created worktree for $AGENT
  branch:  $BRANCH
  base:    $base_sha ($BASE)
  path:    $DIR
Next:
  cd "$DIR"
  scripts/agent_worktree.sh check
  # read AGENTS.md, docs/agent/REPO_MAP.md, docs/agent/CURRENT_STATE.md, your task file
  scripts/agent_validate.sh --tests "<targeted tests>"   # before handoff
EOF
}

cmd_check() {
  local git_dir branch base
  git_dir=$(git rev-parse --absolute-git-dir)
  if [ "$git_dir" = "$COMMON_DIR" ]; then
    die "this is the main/shared worktree ($MAIN_ROOT). Create your own: scripts/agent_worktree.sh create <task-slug> --agent <name>"
  fi
  branch=$(git symbolic-ref --short -q HEAD) || die "HEAD is detached; agents work on their own agent/<agent>/<task> branch"
  case "$branch" in
    agent/*/*) ;;
    *) die "branch '$branch' is not an agent branch (agent/<agent>/<task>); never commit on shared branches" ;;
  esac
  base=$(git config --get "branch.$branch.agentbase" || echo "unrecorded")
  echo "OK: isolated worktree $(git rev-parse --show-toplevel)"
  echo "    branch $branch, base $base"
}

cmd_list() {
  git worktree list | grep -F '[agent/' || echo "(no agent worktrees)"
}

cmd_remove() {
  parse_names "$@"
  git worktree list --porcelain | grep -Fxq "worktree $DIR" || die "$DIR is not a registered worktree"
  if [ -n "$(git -C "$DIR" status --porcelain)" ]; then
    die "$DIR has uncommitted changes; commit or discard them yourself first"
  fi
  local upstream ahead
  if upstream=$(git -C "$DIR" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null); then
    ahead=$(git -C "$DIR" rev-list --count "$upstream..HEAD")
    [ "$ahead" = "0" ] || echo "note: $ahead commit(s) not pushed to $upstream; they stay on branch $BRANCH"
  else
    echo "note: $BRANCH has no upstream; its commits stay on the local branch"
  fi
  git worktree remove "$DIR"
  echo "Removed worktree $DIR. Branch $BRANCH is kept; delete it yourself once merged (git branch -d $BRANCH)."
}

case "${1:-}" in
  create) shift; cmd_create "$@" ;;
  check) shift; cmd_check ;;
  list) shift; cmd_list ;;
  remove) shift; cmd_remove "$@" ;;
  -h|--help|help) usage 0 ;;
  *) usage 2 ;;
esac
