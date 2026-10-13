#!/usr/bin/env bash
# Pre-handoff checks for an agent's worktree. Read-only: it changes no file
# and makes no git write; a corpus run writes only to a temporary directory.
#
#   scripts/agent_validate.sh [--tests "<pytest paths/args>"] [--quick]
#                             [--base <ref>] [--allow-main] [--allow-dirty]
#                             [--corpus <dir> --manifest <file> [--baseline <ref>]]
#
#   --tests      targeted tests to run first (quoted, passed to pytest)
#   --quick      skip the full suite and the golden ratchet (not for handoff)
#   --base       what the branch is compared with (default: the base recorded
#                when the worktree was created, else origin/main, else main)
#   --allow-main run outside an agent worktree (maintainers only)
#   --allow-dirty do not fail on uncommitted changes
#   --corpus/--manifest  run the private corpus gate locally, baseline -> HEAD
#                (default baseline: the base). Without them the gate is
#                reported NOT RUN; see docs/corpus-gate.md and
#                docs/cloud-corpus-gate.md.
set -uo pipefail

TESTS="" QUICK=0 BASE="" ALLOW_MAIN=0 ALLOW_DIRTY=0 CORPUS="" MANIFEST="" BASELINE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --tests) TESTS="${2:-}"; shift 2 ;;
    --quick) QUICK=1; shift ;;
    --base) BASE="${2:-}"; shift 2 ;;
    --allow-main) ALLOW_MAIN=1; shift ;;
    --allow-dirty) ALLOW_DIRTY=1; shift ;;
    --corpus) CORPUS="${2:-}"; shift 2 ;;
    --manifest) MANIFEST="${2:-}"; shift 2 ;;
    --baseline) BASELINE="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,/^set -uo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "agent_validate: unknown option $1" >&2; exit 2 ;;
  esac
done

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "not in a git repository" >&2; exit 2; }
cd "$ROOT"
PY=${PYTHON:-python}
RESULTS=()
FAILED=0

record() {  # record <PASS|FAIL|WARN|NOT RUN|SKIP> <step> [detail]
  RESULTS+=("$(printf '%-8s %-22s %s' "$1" "$2" "${3:-}")")
  [ "$1" = "FAIL" ] && FAILED=1
  return 0
}

step() { echo; echo "== $1"; }

# 1. Where am I?
step "worktree"
if bash "$HERE/agent_worktree.sh" check; then
  record PASS worktree
elif [ "$ALLOW_MAIN" = 1 ]; then
  record WARN worktree "not an agent worktree (--allow-main)"
else
  record FAIL worktree "run inside your own agent worktree"
fi

# 2. Against what?
BRANCH=$(git symbolic-ref --short -q HEAD || echo "")
if [ -z "$BASE" ]; then
  BASE=$( [ -n "$BRANCH" ] && git config --get "branch.$BRANCH.agentbase" || true )
  if [ -z "$BASE" ]; then
    if git rev-parse --verify --quiet "origin/main^{commit}" >/dev/null; then BASE=origin/main; else BASE=main; fi
  fi
fi
if ! git rev-parse --verify --quiet "$BASE^{commit}" >/dev/null; then
  record FAIL base "base '$BASE' is not a commit here"
  BASE=HEAD
fi
MERGE_BASE=$(git merge-base "$BASE" HEAD 2>/dev/null || git rev-parse HEAD)
echo "base $BASE -> merge-base $MERGE_BASE"

# 3. Git sanity
step "git status and whitespace"
DIRTY=$(git status --porcelain)
if [ -n "$DIRTY" ]; then
  echo "$DIRTY"
  if [ "$ALLOW_DIRTY" = 1 ]; then record WARN uncommitted "changes not committed"; else record FAIL uncommitted "commit before handoff"; fi
else
  record PASS uncommitted
fi
if git diff --check "$MERGE_BASE" -- . && git diff --check -- .; then
  record PASS whitespace
else
  record FAIL whitespace "git diff --check"
fi

# 4. Private data and generated files
step "private data and generated files"
CHANGED=$( { git diff --name-only --diff-filter=AMR "$MERGE_BASE" HEAD; \
             git ls-files --others --exclude-standard; \
             git diff --cached --name-only --diff-filter=AMR; \
             git diff --name-only --diff-filter=AMR; } | sort -u )
BAD=()
while IFS= read -r path; do
  [ -z "$path" ] && continue
  case "$path" in
    *.pdf|*.PDF|*.xlsx|*.xls) BAD+=("$path: document or workbook file") ;;
    .env|.env.*) [ "$path" = ".env.example" ] || BAD+=("$path: secrets file") ;;
    data/profiles/*|data/telemetry/*|uploads/*) BAD+=("$path: local data, never committed") ;;
    tests/*) ;;
    *manifest*.json|*labels*.json|*.jsonl) BAD+=("$path: corpus manifest/labels/recordings/telemetry belong outside the repo") ;;
  esac
  if [ -f "$path" ] && [ "$(wc -c < "$path")" -gt 1048576 ]; then
    BAD+=("$path: larger than 1 MB")
  fi
done <<< "$CHANGED"
if [ ${#BAD[@]} -gt 0 ]; then
  printf '  %s\n' "${BAD[@]}"
  record FAIL private-data "${#BAD[@]} file(s)"
else
  record PASS private-data
fi

# 5. Compiles
step "compile"
if "$PY" -m compileall -q core tools app.py tests >/dev/null; then record PASS compile; else record FAIL compile; fi

# 6. Targeted tests
if [ -n "$TESTS" ]; then
  step "targeted tests: $TESTS"
  # shellcheck disable=SC2086
  if "$PY" -m pytest -q -p no:cacheprovider $TESTS; then record PASS targeted "$TESTS"; else record FAIL targeted "$TESTS"; fi
else
  record "NOT RUN" targeted "none given (--tests)"
fi

# 7. Full suite and golden ratchet
if [ "$QUICK" = 1 ]; then
  record SKIP full-suite "--quick (not acceptable for handoff)"
  record SKIP golden "--quick"
else
  step "full suite"
  if "$PY" -m pytest -q -p no:cacheprovider; then record PASS full-suite; else record FAIL full-suite; fi
  step "golden accuracy ratchet"
  if "$PY" -m tests.golden.baseline; then record PASS golden; else record FAIL golden "a carrier dropped below its recorded accuracy"; fi
fi

# 8. Private corpus gate
if [ -n "$CORPUS" ] && [ -n "$MANIFEST" ]; then
  step "private corpus gate"
  OUT=$(mktemp -d "${TMPDIR:-/tmp}/losslift-gate-XXXXXX")
  if "$PY" -m tools.corpus_gate run --baseline "${BASELINE:-$MERGE_BASE}" --candidate HEAD \
      --corpus "$CORPUS" --manifest "$MANIFEST" --out "$OUT"; then
    record PASS corpus-gate "report: $OUT/report.txt"
  else
    record FAIL corpus-gate "explain every change (docs/corpus-gate.md); report: $OUT/report.txt"
  fi
else
  record "NOT RUN" corpus-gate "no --corpus/--manifest; required for extraction/reconciliation changes"
fi

echo
echo "== summary ($(git rev-parse --short HEAD) on ${BRANCH:-detached}, base $(git rev-parse --short "$MERGE_BASE"))"
printf '%s\n' "${RESULTS[@]}"
exit "$FAILED"
