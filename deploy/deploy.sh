#!/usr/bin/env bash
# Argus 一键部署：部署内容以 GitHub 上的 main 为准，服务器从 git 拉取构建，不走 rsync。
#   1. 校验本地 main 干净，push 到 origin
#   2. SSH 到服务器执行 deploy/remote-deploy.sh：git 拉取 + docker compose 重建上线 + 健康检查
#
# 用法：
#   deploy/deploy.sh              # 完整部署：push + 远端拉取构建
#   deploy/deploy.sh --no-push    # 跳过 push（远端分支已就绪时）
#   deploy/deploy.sh --dry-run    # 只打印将执行的操作，不做任何变更
#
# 服务器地址等默认值可用 deploy/deploy.env 覆盖（模板见 deploy.env.example，已 gitignore）。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BRANCH="main"

if [ -f "$SCRIPT_DIR/deploy.env" ]; then
  # shellcheck source=/dev/null
  . "$SCRIPT_DIR/deploy.env"
fi
DEPLOY_HOST="${ARGUS_DEPLOY_HOST:-47.108.74.28}"
DEPLOY_USER="${ARGUS_DEPLOY_USER:-root}"
DEPLOY_DIR="${ARGUS_DEPLOY_DIR:-/opt/argus}"
# 服务器 git 拉取用的仓库地址，默认取本地 origin；服务器拉私有库需自行具备读取权限（deploy key 等）。
DEPLOY_REPO_URL="${ARGUS_DEPLOY_REPO_URL:-$(git -C "$REPO_ROOT" remote get-url origin)}"
DEPLOY_SSH_OPTS="${ARGUS_DEPLOY_SSH_OPTS:--o PubkeyAuthentication=no}"

DRY_RUN=0
DO_PUSH=1
for arg in "$@"; do
  case "$arg" in
    --no-push) DO_PUSH=0 ;;
    --dry-run) DRY_RUN=1 ;;
    -h | --help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "未知参数：$arg（支持 --no-push / --dry-run）" >&2; exit 1 ;;
  esac
done

run() {
  if [ "$DRY_RUN" = 1 ]; then echo "[dry-run] $*"; else "$@"; fi
}

cd "$REPO_ROOT"

current="$(git branch --show-current)"
if [ "$current" != "$BRANCH" ]; then
  echo "错误：当前在 $current 分支；部署内容以远端 $BRANCH 为准，请切到 $BRANCH 执行。" >&2
  exit 1
fi
if [ -n "$(git status --porcelain)" ]; then
  echo "错误：工作区有未提交改动；git 拉取部署只包含已提交并 push 的内容：" >&2
  git status --short >&2
  exit 1
fi

echo "==> 本地仓库：$REPO_ROOT"
if [ "$DO_PUSH" = 1 ]; then
  run git push origin "$BRANCH"
else
  echo "==> 跳过 push（--no-push），按远端 $BRANCH 当前状态部署"
fi

# SSH 登录参数。默认禁用公钥（本机 key 带口令会卡住），走密码；SSHPASS + sshpass 可非交互。
# shellcheck disable=SC2206
SSH_OPTS_ARR=($DEPLOY_SSH_OPTS)
SSH_PREFIX=()
if [ -n "${SSHPASS:-}" ] && command -v sshpass >/dev/null 2>&1; then
  SSH_PREFIX=(sshpass -e)
  echo "==> 使用 sshpass 非交互登录"
fi

REMOTE_ENV="$(printf 'ARGUS_DIR=%q ARGUS_REPO_URL=%q ARGUS_BRANCH=%q' "$DEPLOY_DIR" "$DEPLOY_REPO_URL" "$BRANCH")"

if [ "$DRY_RUN" = 1 ]; then
  echo "[dry-run] ${SSH_PREFIX[*]:-} ssh ${SSH_OPTS_ARR[*]} $DEPLOY_USER@$DEPLOY_HOST \"$REMOTE_ENV bash -s\" < deploy/remote-deploy.sh"
  echo "---------- 将在服务器执行的 deploy/remote-deploy.sh ----------"
  cat "$SCRIPT_DIR/remote-deploy.sh"
  exit 0
fi

echo "==> 远程：$DEPLOY_USER@$DEPLOY_HOST（部署目录 $DEPLOY_DIR）"
# shellcheck disable=SC2029
"${SSH_PREFIX[@]+"${SSH_PREFIX[@]}"}" ssh "${SSH_OPTS_ARR[@]}" "$DEPLOY_USER@$DEPLOY_HOST" \
  "$REMOTE_ENV bash -s" < "$SCRIPT_DIR/remote-deploy.sh"

echo "==> 上线地址：http://$DEPLOY_HOST"
