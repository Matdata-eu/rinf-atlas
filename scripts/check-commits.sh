#!/usr/bin/env bash
# Check that commit subjects follow Conventional Commits (https://www.conventionalcommits.org).
# Usage: scripts/check-commits.sh [<range>]   (default: origin/main..HEAD)
# Merge commits are skipped.
set -euo pipefail

range="${1:-origin/main..HEAD}"
types='feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert'
pattern="^(${types})(\([a-z0-9._/-]+\))?!?: [^ ].*"

commits=$(git log --no-merges --format=$'%H\t%s' "$range")

bad=0
while IFS=$'\t' read -r sha subject; do
  [ -z "$sha" ] && continue
  if ! grep -qE "$pattern" <<<"$subject"; then
    echo "::error::${sha:0:7} \"${subject}\" is not a Conventional Commit subject"
    bad=1
  fi
done <<<"$commits"

if [ "$bad" -ne 0 ]; then
  echo "Use <type>(<optional scope>): <description>, with type one of ${types//|/, }."
  echo "Reword with: git rebase -i <base>, then mark the commits as 'reword'."
  exit 1
fi
echo "All commit subjects in ${range} follow Conventional Commits."
