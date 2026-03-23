#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "${REPO_ROOT}"
git config core.hooksPath .githooks

echo "✅ Git hooks installed. core.hooksPath=.githooks"
echo "Pre-commit now enforces PROJECT_CONTEXT.md updates for code/config/docs commits."
