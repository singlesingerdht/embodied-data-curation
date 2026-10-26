#!/usr/bin/env bash
# After GitHub CLI authentication, create and push this concrete local repository.
set -euo pipefail
edc_repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$edc_repo_root"
command -v gh >/dev/null || { echo '需要先安装 GitHub CLI（gh）并执行 gh auth login。' >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo '缺少 GitHub API 身份，请先执行 gh auth login。' >&2; exit 1; }
if git remote get-url origin >/dev/null 2>&1; then
  echo 'origin 已存在；请核对其仓库与私有状态，本脚本不会替换远端。' >&2
  exit 1
fi
if [[ -n "$(git status --porcelain)" ]]; then
  echo '请先提交本地变更；本脚本仅发布已提交的版本。' >&2
  exit 1
fi
gh repo create embodied-data-curation --private --source . --remote origin --push
gh repo view --json nameWithOwner,visibility,url
