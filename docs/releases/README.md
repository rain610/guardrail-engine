# docs/releases —— 发布说明约定（2026-09-15 起）

- 每个版本的说明写成 `docs/releases/<tag>.md`（例：`v0.1.3-dev.md`），作为 GitHub Release 正文底稿；
  由 `.github/workflows/release.yml` 在推送 `v*` tag 时自动取用（不存在则退回 GitHub 自动生成 changelog）。
- 模板：`_TEMPLATE.md`。「校验（SHA1 / 大小 / 文件数）」与「Full Changelog」对比链接由 CI 自动追加，不用手写。
- 发布流程（2 步）：① 改 `VERSION` + 写本目录说明 → ② 打 tag 并**推给两个远程**：
  `git tag v<版本> && git push origin v<版本> && git push gitlab v<版本>`。
  GitHub Actions 自动完成：全量自检 → 构建安装包 → 创建 Release（含 ASCII 附件）。
- 内网 GitLab **无 runner**（2026-09-15 实测：Specific/Shared/Group runners 三栏全空，实例明确写着
  "does not provide any shared runners yet"）→ 内网 Release **不会自动创建**，且 Release 界面
  **只能放链接、不能上传附件**（旧文档写"上传附件"有误，已更正）。
  正确做法：New release → 选 tag → 正文粘 `docs/releases/<tag>.md` → **Assets → Links** 指向仓库内镜像：
  `https://resources.ehsway.com:8929/developer/agentguard/-/raw/main/docs/releases/assets/agentguard-<tag>.zip`
  （镜像由 `docs/releases/assets/` 提供，与 GitHub 附件同一文件、已核 sha256；见该目录 README。）
- **内网版正文改写清单（建内网 Release 时照做，2 处）**：
  ① 「下载渠道」行 → 指向本 Release 的 Assets（`agentguard-<tag>.zip`，与 GitHub 附件同一文件；内网镜像就是 ASCII 名）；
  ② 「校验」行 → 补一行内网镜像的 `sha256` + 字节数（取值见 `docs/releases/assets/README.md` 的表）。
  模板 `_TEMPLATE.md` 末尾有同样的备注；GitHub 侧正文不用改（CI 自动生成的那版本来就是对的）。
- 历史回填：`v0.1.1-dev.md`、`v0.1.2-dev.md`（当时由 `dist/install-package/Release说明-*.md` 承担）；
  v0.1.0 当时未写 Release 说明，无回填。
  ⚠ `dist/` 在 `.gitignore` 里（`dist/install-package/` 是**打包机上的本地产物**，不进仓库）——
  所以别去 clone 里找那两个 `Release说明-*.md`，按图索骥必然找不到（评审 v13 低 24）。
  它们的内容**已经回填进本目录**（`v0.1.1-dev.md` / `v0.1.2-dev.md`），要看就看这两份。
