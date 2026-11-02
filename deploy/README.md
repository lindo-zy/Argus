# Argus 部署

部署内容以 GitHub 上的 `main` 为准：本机执行 `deploy/deploy.sh`，脚本 push 后 SSH 到服务器 `git pull` 并 `docker compose` 构建上线。不走 rsync，服务器代码一律来自 git。

## 组成

| 文件 | 作用 |
| --- | --- |
| `deploy.sh` | 本机执行的一键部署入口（push + 远程编排） |
| `remote-deploy.sh` | 在服务器上执行：git 拉取、swap 检查、compose 构建上线、健康检查 |
| `docker-compose.yml` | 三容器编排：`argus-db` / `argus-backend` / `argus-frontend`（项目名固定 `argus`） |
| `backend.Dockerfile` / `frontend.Dockerfile` | 前后端镜像；构建上下文仍是 `../backend`、`../frontend` |
| `deploy.env.example` | 可覆盖配置模板；复制为 `deploy.env`（已 gitignore） |

## 前置条件

- 本机：git、ssh；非交互自动化另需 sshpass（密码经 `SSHPASS` 环境变量传入，不要写入任何文件）。
- 服务器：git、docker compose，且能用所配仓库地址拉取代码。私有仓库建议在服务器生成只读 deploy key 加到 GitHub 仓库的 Deploy keys，或改用 https + 凭据（`ARGUS_DEPLOY_REPO_URL` 覆盖）。
- 本机默认 `-o PubkeyAuthentication=no` 走密码登录（本机 key 带口令会卡住）；已配好可用 key 时用 `ARGUS_DEPLOY_SSH_OPTS` 覆盖。
- 服务器内存小：swap 不足时脚本会幂等创建 2G `/swapfile`（不写入 fstab，重启后首次部署自动重建）。

## 用法

```sh
deploy/deploy.sh              # 完整部署：push main + 服务器拉取构建 + 健康检查
deploy/deploy.sh --no-push    # 跳过 push（远端 main 已就绪时）
deploy/deploy.sh --dry-run    # 只打印将执行的操作
```

要求本地在 `main` 分支且工作区干净——未提交的改动不会上线。`--no-push` 时按远端 `main` 当前状态部署。

## 首次部署到既有服务器

`/opt/argus` 历史上由 rsync 同步、不是 git 仓库：首次执行会把原目录整体备份为 `/opt/argus.bak.<时间戳>` 后重新克隆。容器与数据不受影响——compose 项目名固定为 `argus`，`argus_pgdata` 卷照常复用。确认稳定后可手动删除备份目录。

## 直接在服务器上执行

```sh
env ARGUS_DIR=/opt/argus ARGUS_REPO_URL=<仓库地址> ARGUS_BRANCH=main \
  bash deploy/remote-deploy.sh
```

## 本地开发 Jenkins（动态流水线验证用）

仅本机回环，供 `scripts/verify_jenkins_e2e.py` 与动态流水线联调使用：

```sh
docker compose -f deploy/jenkins.dev.yml up -d      # 端口 127.0.0.1:18090
# 首次启动后生成 API Token（用户 argus，密码 ARGUS_JENKINS_PASSWORD，默认 argus-local-demo）：
python3 - <<'PY'
import base64, json, urllib.request
base = "http://127.0.0.1:18090"
auth = base64.b64encode(b"argus:argus-local-demo").decode()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
req = urllib.request.Request(base + "/crumbIssuer/api/json", headers={"Authorization": "Basic " + auth})
crumb = json.loads(opener.open(req).read())
req = urllib.request.Request(base + "/user/argus/descriptorByName/jenkins.security.ApiTokenProperty/generateNewToken",
    data=b"newTokenName=argus-dev",
    headers={"Authorization": "Basic " + auth, crumb["crumbRequestField"]: crumb["crumb"],
             "Content-Type": "application/x-www-form-urlencoded"})
print(json.loads(opener.open(req).read())["data"]["tokenValue"])
PY
```

后端连接配置（Jenkins 未配置时自动退回演示模拟）：

```sh
ARGUS_JENKINS_URL=http://127.0.0.1:18090
ARGUS_JENKINS_USERNAME=argus
ARGUS_JENKINS_TOKEN=<上一步生成的 token>
```

注意：XML 提交必须带 `charset=utf-8`（否则中文会被 Jenkins 错误解码）；config.xml 回读经
XStream 重新排版，同步幂等比对基于语义签名（描述/参数/构建步骤）而非字节。
