# 构建与供应链安全

生产镜像由仓库根 `Dockerfile` 的三个阶段构建：Node 阶段根据 `package-lock.json` 重建 Vue 管理端，Python 依赖阶段只安装带 SHA-256 hash 的 `requirements.lock`，最终 Alpine 运行时只接收构建产物和运行依赖。Node、Python 基础镜像均固定到不可变 digest，最终镜像不包含 npm、编译工具或开发测试依赖。

## 更新依赖锁

`pyproject.toml` 仍是 Python 直接依赖的声明源。使用固定 digest 的 Linux/Python 3.12 容器和固定版本 pip-tools 生成生产、开发锁：

```powershell
./scripts/update-python-locks.ps1
./scripts/update-python-locks.ps1 -Check
```

修改 `pyproject.toml` 后必须重新生成两个 lockfile，并运行任务 01 的完整质量门禁。生产 Dockerfile 使用 `--require-hashes --only-binary=:all:`；缺少 hash、版本或 Linux wheel 都会让构建失败。Dependabot 每周检查 Python、npm、基础镜像和 GitHub Actions；升级 PR 必须同时更新锁文件和通过全部回归。

## SBOM、扫描和发布

`.github/workflows/supply-chain.yml` 对每个 Pull Request 和提交构建最终生产 target，生成 SPDX JSON SBOM，并以 Trivy 扫描最终镜像的 OS 与 Python 包。任何未批准的 Critical 或 High 漏洞都会令 `image-sbom-vulnerability-gate` 和最终 `supply-chain-gate` 失败；报告与 SBOM 保留 30 天。

只有以 `v` 开头的版本标签在扫描通过后才会推送 GHCR。发布构建附带 BuildKit SBOM 与 provenance attestation，随后使用 GitHub OIDC 和 Cosign keyless 签名镜像摘要。生产部署只接受 `ghcr.io/<owner>/<repo>@sha256:<digest>`，不得使用浮动 tag。

发布前验证摘要、签名和证明：

```bash
cosign verify \
  --certificate-identity-regexp '^https://github.com/<owner>/<repo>/.github/workflows/supply-chain.yml@refs/tags/v' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
  ghcr.io/<owner>/<repo>@sha256:<digest>

cosign verify-attestation \
  --type spdxjson \
  --certificate-identity-regexp '^https://github.com/<owner>/<repo>/.github/workflows/supply-chain.yml@refs/tags/v' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
  ghcr.io/<owner>/<repo>@sha256:<digest>
```

## 漏洞例外

默认 `.trivyignore.yaml` 没有例外。确需临时接受风险时，单独提交安全审批 PR；每条记录必须限定 CVE 与受影响路径/包，写明是否可利用、补偿控制、责任人和不超过 30 天的 `expired_at`。Critical 例外最长 7 天，High 最长 30 天。到期项不得续期而不重新评审；Trivy 会忽略已过期例外，使门禁重新失败。

供应链门禁故障时不得创建或移动生产标签。若新摘要上线后出现问题，把 `CREATIVE_STUDIO_IMAGE` 回退到上一条已经验签且保留 SBOM/扫描报告的 digest，再按正常 Compose 流程部署；不得从未知本地镜像临时恢复。
