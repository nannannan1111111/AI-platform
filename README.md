# 乐云工坊 SaaS（独立版）

这是从当前部署版本中独立整理出的 Python/FastAPI SaaS 工程。它包含 SaaS 后端、Alembic 数据库迁移、账户与管理员 Web UI、完整经典/智能画布、运行所需的受控静态资源和 Docker 部署文件。

本目录只保留当前 SaaS 生产入口，不包含旧版根入口 `main.py`、旧 Python 兼容应用 `creative_studio/`、Go `saas/` 工程或本地一键部署入口，也不包含任何本机数据库、生成媒体、Provider 密钥、日志或测试缓存。

## 目录

- `backend/app/`：SaaS 业务、HTTP API、生产装配与 Web UI。
- `backend/alembic/`：PostgreSQL 数据库迁移。
- `backend/tests/`：后端自动化测试。
- `frontend/admin/`：Vue 3 + TypeScript 管理后台源码；由 Vite 构建后交给 FastAPI 静态托管。
- `static/`：SaaS 当前仍需挂载的编辑器资源；不包含旧根 HTML 入口。
- `deploy/`：服务器生产 Compose 配置与环境变量示例。
- `docs/deployment-and-operations.md`：生产部署和运维说明。

## 管理后台前端

管理后台及账户、钱包、任务、模型目录、资产库、LLM 设置等普通用户页面已迁移到 Vue 3。FastAPI API、登录态和侧边栏保持不变；图片生成、局部重绘与画布继续由原有 JavaScript 承载。

首次安装依赖并构建：

```powershell
cd frontend/admin
npm install
npm run build
```

日常修改后可执行 `npm run check` 做 TypeScript 检查，再执行 `npm run build`。构建产物写入 `backend/app/webui/static/admin-vue/`，随 FastAPI Web 静态资源一同发布，不需要单独部署前端服务。

## 生产入口

容器入口为：

```text
app.runtime:create_production_app
```

生产部署前请阅读 `docs/deployment-and-operations.md`，并将数据库、媒体目录和 Provider 密钥目录作为同一恢复点备份。
