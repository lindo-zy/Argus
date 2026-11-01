#!/usr/bin/env bash
# Argus 服务器端部署脚本：在服务器上执行（deploy.sh 会通过 SSH 传入并运行，也可手动执行）。
# 需要环境变量：ARGUS_DIR（部署目录）、ARGUS_REPO_URL（仓库地址）、ARGUS_BRANCH（分支）。
# 流程：确保目录为 git 仓库（否则备份后克隆）→ git pull --ff-only → 补 swap → compose 构建上线 → 健康检查。
set -euo pipefail

: "${ARGUS_DIR:?需要 ARGUS_DIR}"
: "${ARGUS_REPO_URL:?需要 ARGUS_REPO_URL}"
ARGUS_BRANCH="${ARGUS_BRANCH:-main}"

echo "==> [$(hostname)] 部署目录 $ARGUS_DIR（分支 $ARGUS_BRANCH，仓库 $ARGUS_REPO_URL）"

command -v git >/dev/null || {
  echo "错误：服务器未安装 git，请先安装（如 apt-get install -y git）" >&2
  exit 1
}
docker compose version >/dev/null 2>&1 || {
  echo "错误：服务器 docker compose 不可用" >&2
  exit 1
}

# 首次部署：目录不是 git 仓库（历史上由 rsync 同步）时，整体备份后重新克隆。
if [ ! -d "$ARGUS_DIR/.git" ]; then
  if [ -d "$ARGUS_DIR" ] && [ -n "$(ls -A "$ARGUS_DIR" 2>/dev/null)" ]; then
    bak="$ARGUS_DIR.bak.$(date +%Y%m%d%H%M%S)"
    echo "==> $ARGUS_DIR 不是 git 仓库，备份为 $bak 后重新克隆"
    mv "$ARGUS_DIR" "$bak"
  fi
  git clone --single-branch --branch "$ARGUS_BRANCH" "$ARGUS_REPO_URL" "$ARGUS_DIR" || {
    echo "错误：克隆失败。私有仓库需让服务器有读取权限（推荐只读 deploy key，或 https + 凭据）。" >&2
    exit 1
  }
fi

cd "$ARGUS_DIR"
echo "==> git pull --ff-only origin $ARGUS_BRANCH"
git pull --ff-only origin "$ARGUS_BRANCH"

# 服务器内存小：构建前确保 swap 足够（/swapfile 未写入 fstab，重启后这里会幂等重建）。
swap_mb="$(free -m | awk '/^Swap:/{print $2; exit}')"
if [ "${swap_mb:-0}" -lt 2000 ] && [ ! -f /swapfile ]; then
  echo "==> swap 不足，创建 2G /swapfile（不写入 fstab，重启失效）"
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
fi

echo "==> docker compose 构建并上线"
docker compose -f deploy/docker-compose.yml up -d --build
docker compose -f deploy/docker-compose.yml ps

# 健康检查：nginx 同源反代 /healthz 到后端。
if command -v curl >/dev/null 2>&1; then
  echo "==> 健康检查 http://127.0.0.1/healthz"
  ok=0
  for _ in $(seq 1 30); do
    if curl -fsS http://127.0.0.1/healthz; then
      ok=1
      break
    fi
    sleep 2
  done
  if [ "$ok" != 1 ]; then
    echo "错误：/healthz 60 秒内未通过，最近日志：" >&2
    docker compose -f deploy/docker-compose.yml logs --tail=50 backend frontend >&2
    exit 1
  fi
  echo ""
else
  echo "==> 服务器无 curl，跳过健康检查"
fi

echo "==> 部署完成"
