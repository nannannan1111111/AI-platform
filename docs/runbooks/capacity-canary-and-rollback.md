# 容量、灰度与回滚

本 Runbook 将容量、灰度停止条件和应用回滚约束固化为机器可校验合同。它不执行 DNS、负载均衡、数据库 downgrade 或真实流量切换；真实演练仍必须在与生产同规格的预发布环境完成。

## 拓扑边界

当前首发拓扑是单 CVM、单公网入口、PostgreSQL 队列和共享本机媒体卷。它支持：

- 在隔离预发布环境先让内部账号验收候选摘要。
- 在同一迁移 head 下，把公网应用切换到候选不可变摘要并观察，超过阈值后切回上一摘要。
- Worker 依靠 PostgreSQL advisory lock 防止同一任务被多个进程同时认领；优雅停止期间已提交给 Provider 的请求继续遵循 `running`/`unknown` 事实，不能重新提交。

它不支持真正的公网 10% 双版本分流。要执行 `limited` 百分比阶段，必须先提供两个可独立寻址的应用实例和受控负载均衡策略，并解决共享媒体存储与总连接预算；没有这些能力时，`traffic_percent` 只是批准的阶段目标，不能记录为已执行。

## 容量合同

复制 `deploy/tencent-cloud/canary-plan.example.json` 到受控发布目录，按真实规格填写。示例值不是生产 SLO，也不是费用批准。

应用最坏连接预算为：

```text
Web 副本数 x Web 进程数 x (Web 池大小 + Web overflow)
+ Worker 副本数 x (Worker 池大小 + Worker overflow)
```

`operations_reserve` 必须至少是应用预算的 20% 或 10 条连接中的较大值，且 `database_max_connections` 必须覆盖应用预算与预留。每个灰度阶段的数据库停止阈值不得侵占该预留。

发布前用候选和上一版本的不可变摘要验证合同并保存状态：

```bash
cd backend
python scripts/deployment_contract.py \
  --plan ../deploy/tencent-cloud/canary-plan.json \
  --image '<private-tcr>/application@sha256:<candidate>' \
  --migration-head 0061_password_reset_tokens \
  --previous-image '<private-tcr>/application@sha256:<previous>' \
  --previous-migration-head 0061_password_reset_tokens \
  --snapshot /srv/infinite-canvas/releases/<candidate>/deployment-state.json
```

迁移 head 不同时默认拒绝生成可直接回滚的快照。`--allow-schema-incompatible` 只证明存在独立审批引用，不能生成普通应用回滚快照，也不会执行数据库 downgrade。

## 分层容量复验

对每个路径分别保存 JSON 结果，不把 Bearer Token、URL、响应体或业务 ID 写入附件。认证只读路径的 Token 通过 `CAPACITY_BEARER_TOKEN` 环境变量注入。

```bash
cd backend
python scripts/capacity_smoke.py https://staging.example.com/readyz \
  --concurrency 100 --requests 1000 \
  --max-failure-rate-percent 0 --max-p95-ms 2500 --min-rps 50 --json

export CAPACITY_BEARER_TOKEN='<从受控测试会话注入>'
python scripts/capacity_smoke.py https://staging.example.com/api/v1/auth/me \
  --concurrency 20 --requests 500 \
  --max-failure-rate-percent 1 --max-p95-ms 2500 --json
```

就绪探针、目录、会话读取和受控媒体读取可以用该 GET 探针。登录、画布保存、任务提交、SSE 与支付会改变状态或产生长连接，必须用专用测试账号按 `staging-acceptance.md` 控制数据和费用，不能通过把 Token 或请求体写入容量脚本参数来绕过保护。

HTTP RPS 不能替代 Provider 吞吐。每个观察窗口还必须从 Prometheus、PostgreSQL、Provider 控制台和账务事实收集：

- HTTP 请求量、错误率和 p95。
- 数据库连接峰值及保留余量。
- 队列最老任务年龄、Worker 利用率和待处理数量。
- Provider 每分钟完成数、配额错误和本阶段实际费用。

## 灰度判定

把一个观察窗口的聚合值写入受控 JSON，结构参考 `deploy/tencent-cloud/canary-observation.example.json`，然后执行：

```bash
python scripts/deployment_contract.py \
  --plan ../deploy/tencent-cloud/canary-plan.json \
  --image '<private-tcr>/application@sha256:<candidate>' \
  --migration-head 0061_password_reset_tokens \
  --previous-image '<private-tcr>/application@sha256:<previous>' \
  --previous-migration-head 0061_password_reset_tokens \
  --observation ../deploy/tencent-cloud/canary-observation.json
```

输出 `decision=promote` 才允许进入下一阶段。以下任一情况输出 `decision=stop`：样本不足、错误率或 p95 超限、队列年龄超限、数据库连接侵占预留、Provider 吞吐不足或费用超过上限。不得通过修改已经批准的计划来让失败观测变绿；阈值变更需要新的审批记录。

## 停止与回滚

1. 停止进入下一阶段，记录停止时间、候选摘要、阶段、请求 ID 和违反的指标名；不记录 Token、提示词、图片或 Provider 密钥。
2. 阻止新流量进入候选 Web，按计划给 Worker 留出优雅停止时间。不得强杀后重新提交已经进入 `running` 或 `unknown` 的任务。
3. 确认候选和上一版本迁移 head 相同，再使用上一已验签摘要执行现有 `deploy-release.sh`。脚本仍按验签、Compose、迁移、启动和就绪顺序执行。
4. 在 `maximum_duration_seconds` 内恢复公网 `/readyz`。超时即升级为事故，不能修改时间记录后宣称达标。
5. 在 `post_rollback_observation_seconds` 内核对 queued/running/unknown 任务、额度冻结/结算/释放、媒体数量、支付事件和 Provider 请求 ID，确认没有重复提交、重复计费或孤立媒体。
6. 保存回滚开始/结束时间、上一摘要、迁移 head、恢复点和脱敏观测。数据库 downgrade、DNS/IaC 回退及备份恢复分别走独立审批和 Runbook。

## 完成条件

- 同规格环境的分层容量结果满足已批准门槛，数据库仍保留合同要求的运维连接。
- 每个已执行灰度阶段都有完整观察窗口和自动判定；未具备百分比分流能力时必须明确标为未执行。
- 至少一次主动触发停止阈值，并在回滚目标时间内恢复上一摘要。
- 回滚后任务、账务、媒体和支付事实一致，候选与上一镜像摘要、迁移 head 和证据位置完整。
