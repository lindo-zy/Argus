# Jenkins 流水线联调

支持两种验证方式：独立 HTTP Mock（无需 Jenkins）和真实 Jenkins 上的模拟 Job。
两者均只验证调度协议，不安装系统、不运行测试脚本、不操作设备。页面明确标注来源。

## 独立 HTTP Mock

先按 backend/README.md 安装依赖。以下命令在 `backend/` 执行：

```sh
.venv/bin/python -m app.jenkins_mock --port 18080 --state /tmp/argus-jenkins-mock.sqlite
```

Mock 持久保存 queue/build 与参数，重启继续可查。可选：

- `--queue-delay 2 --build-delay 3`：排队、执行时长（秒）。
- `--fail-node NODE_ID`：指定节点失败，可重复传入。
- `--drop-response-node NODE_ID`：接受任务后断开响应，校验结果不明时通过 ARGUS_TOKEN 对账而不重提。
- `/healthz` 返回提交数量；`/job/JOB/BUILD/consoleText` 返回带 mock 标记的日志。

在另一个终端，配置**明确用于联调的本地开发库**并运行迁移后启动后端；不要使用共享服务或 SSH 转发数据库做联调：

```sh
export ARGUS_DATABASE_URL='postgresql+psycopg://LOCAL_USER@127.0.0.1:LOCAL_PORT/argus_jenkins_preview'
export ARGUS_JENKINS_URL='http://127.0.0.1:18080'
export ARGUS_JENKINS_KIND='mock'
.venv/bin/alembic upgrade head
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

浏览器必须处于 HTTP 后端模式，列表标记为 **Jenkins HTTP Mock**。浏览器内存 Mock 会拒绝 Jenkins 运行。

1. 复制已有流水线，或新建 DAG；旧 XML 副本会生成待补齐镜像和用例的空配置模板。
2. 设置镜像和用例，保存草稿；管理员通过“查看与发布”校验发布，成员可执行已发布版本。
3. 运行时选择默认环境；安装节点已有指定环境时使用该节点配置。
4. 执行详情自动刷新，依次展示节点、queue、build、结果和日志引用。
5. 所有节点收敛后进入 cleaning；记录人工清理复核结论，通过释放，异常隔离。任务失败和网络超时本身都不释放设备。

## 真实 Jenkins 上的模拟 Job

在已授权 Jenkins 中人工创建两个参数化 Pipeline Job：`argus-install`、`argus-cases`，均使用本目录 `Jenkinsfile.mock`。本仓库不会自动创建 Job 或修改共享 Jenkins。

也可使用仓库提供的独立本地真实 Jenkins（仅创建新控制器，不挂载已有 Jenkins 数据）：

```sh
# 仓库根目录执行，仅启动 loopback:18081，不启动或修改 Argus 共享栈。
docker compose -f deploy/docker-compose.jenkins-local.yml up -d
```

此开发实例使用 Jenkins LTS 2.580.2，自动创建两个核心 Freestyle 模拟 Job，无需插件；配置位于 `init-local-mock.groovy`。该实例没有生产身份数据，仅绑定回环用于本机联调，保留默认 CSRF 保护。后端设 `ARGUS_JENKINS_URL=http://127.0.0.1:18081`、`ARGUS_JENKINS_KIND=jenkins`，即通过真实 Jenkins queue、executor 和 build 执行模拟任务。节点 ID 为 `mock-fail` 时模拟失败。不要将此 fixture 用作共享或生产 Jenkins。

配置键：

| 配置键 | 用途 |
| --- | --- |
| `ARGUS_JENKINS_URL` | Jenkins 根地址，可包含 context path |
| `ARGUS_JENKINS_KIND` | `jenkins` 表示真实 Jenkins；`mock` 表示独立协议 Mock |
| `ARGUS_JENKINS_INSTALL_JOB` | 安装受控 Job，默认 `argus-install`，支持 `folder/job` |
| `ARGUS_JENKINS_CASES_JOB` | 用例受控 Job，默认 `argus-cases` |
| `ARGUS_JENKINS_USERNAME` | 服务账号名，通过部署环境注入 |
| `ARGUS_JENKINS_TOKEN` | API token，通过部署环境注入，不写入仓库或页面 |
| `ARGUS_JENKINS_WORKER` | 默认 `1`，`0` 停止本进程调度但仍允许登记意图 |

生产接入使用用户名 + API token 认证。未配置 API 认证时，客户端会获取 crumb 并保留会话 cookie；不会关闭 Jenkins CSRF。账号需读取受控 Job/构建、查看 queue、触发 build。不得将任务配置开放为任意 Job、脚本或 XML。

标准参数固定为：`ARGUS_TOKEN`、`RUN_ID`、`NODE_ID`、`ENV_ID`、`IMAGE_REF`、`DEVICE_IDS`（JSON）、`CASE_REFS`（ID/version JSON）、`PIPELINE_HASH`。Job 必须声明这些参数并保留 ARGUS_TOKEN，以便提交响应丢失后按 queue/构建参数查询。

## 调度保证与边界

- 开始运行时同一事务固定 DAG/内容哈希/参数/环境拓扑/设备快照，创建唯一设备占用和持久任务；同一 actor/requestKey 重放返回同一 run，不同参数报冲突。
- worker 短事务使用 `FOR UPDATE SKIP LOCKED` 认领运行，提交事务后调用 Jenkins，再用 owner token 防止过期 worker 覆盖新结果。
- 每个节点一次提交尝试；提交前先持久化 submitting 意图。超时、响应异常或进程退出后只对账，不自动重提。Jenkins 查询历史有截断时可能长期未知，需恢复可查记录/人工调查，禁止通过模拟终态释放。
- 本轮按依赖串行调度就绪节点；支持分支、汇合、失败停止和失败继续。多个安装节点不能覆盖同一设备，多个用例分支可共用一个上游安装节点。未提供节点重试、取消和并行分片入口。
- 两小时调度时限后不再提交新节点；已经提交的任务继续回查终态，保留资源。本轮不自动中止远端 build。
- 构建成功只代表 Job 成功，不等于用例全部通过；无真实用例明细时 passed/total 保持 0，不虚构证据。只保存实际 build/console 引用，没有附件复制、保留管理或制品摘要校验。
- `IMAGE_REF` 固定当前字符串引用，请使用不可变制品地址；不接入 JFrog、不自动验证镜像内容。真实设备部署与清理由受控 Job/操作人员完成。
- 身份仍是项目现有演示身份机制，不是生产认证。发布仅管理员，运行面向有效成员，Jenkins 运行的清理复核限执行人或管理员。
- 旧 XML 演示运行接口保留兼容，DAG 必须显式使用 Jenkins 模式和 requestKey，不能模拟结束真实调度运行。

## 验证

`tests/test_jenkins_orchestration.py` 使用真实本地 HTTP Mock 与 PostgreSQL 测试库，覆盖成功、失败策略、丢失响应、worker 崩溃恢复、并发 worker、重复请求和资源保护。

```sh
# 必须使用独立 *_test 数据库；普通回归不访问运行中的 MCP 开发服务。
ARGUS_TEST_DATABASE_URL='postgresql+psycopg://LOCAL_USER@127.0.0.1:LOCAL_PORT/argus_jenkins_test' \
  .venv/bin/python -m pytest --ignore=tests/test_mcp.py
```

协议参考：[Jenkins Remote Access API](https://www.jenkins.io/doc/book/using/remote-access-api/)。

## 本次本机验收记录（2026-11-03）

在独立 PostgreSQL `argus_jenkins_test` / `argus_jenkins_preview` 库验证，未使用共享库、转发库或真实设备。

- 后端完整回归 207 项（排除访问运行中开发服务的 `test_mcp.py`）；前端 69 项、生产构建与 SSR 检查通过。
- 独立 HTTP Mock：浏览器实际完成复制、配置、发布、启动、结果自动刷新与清理释放。
- 真实 Jenkins LTS 2.580.2：官方 WAR 通过 SHA-256 验证后，在 loopback:18081 的全新独立控制器运行本目录 Groovy 创建的两个 Freestyle Mock Job，保留默认 CSRF。
- 成功运行 `RUN-90773faad0634692bbb3`：安装 queue 1 / build 1 SUCCESS，用例 queue 2 / build 1 SUCCESS。
- 失败运行 `RUN-9d947583536d4bbdb4f7`：安装 queue 3 / build 2 SUCCESS，用例 queue 4 / build 2 FAILURE，后续节点 blocked，未提交 Job。构建 API 与 consoleText 已交叉核对。
- 新迁移已在空的隔离测试库成功升级；额外 `alembic check` 发现原有 `devices.host` 的数据库注释与 ORM 注释不同，未通过全量元数据一致性检查（本轮新增流水线表未检测到差异）。该注释差异在基线已存在，本轮保留。
- Compose 文件仅做配置解析检查；本机实际验收使用 Java 启动 WAR，未执行共享部署或验证远端 Jenkins。

以上证明本机调度闭环，不证明系统部署、真实用例执行、健康探测、附件归集或生产认证。
