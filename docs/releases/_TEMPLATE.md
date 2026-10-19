# agentguard 内部安装包 · vX.Y.Z-dev（YYYY-MM-DD）

> 一句话定位：这是什么版本、给谁、要不要升级（功能档 / 修复档 / 破坏性档）。

## 本版变化（相对 v<上一版>）

- 大项 1：……
- 大项 2：……

## 安装 / 升级 / 卸载

- 新装：解压 → `python fix_paths.py --harness <你的 agentguard 路径>` → 按《安装说明》安装（CLI / Claude 插件 / OpenClaw 任选）
- 升级：见《安装说明》「五、升级」（路径引用型无需处理；副本型重跑安装命令）
- 卸载：包内 `python uninstall.py --harness <你的路径>`（预演）→ 加 `--yes` 执行

<!-- 以下两行由 CI 自动追加，不要手写：
     校验（CI 自动）：zip SHA1 / 大小 / 文件数
     **Full Changelog**: <上一 tag>...<本 tag> -->

<!-- 内网版（GitLab Release）正文要改 **2 处** —— GitHub 侧不用改（CI 自动生成的那版是对的）：
     ① 「下载渠道」那行 → 指向**本 Release 的 Assets**（内网镜像用的就是 ASCII 名 zip，不是中文名）：
        > 下载渠道：**本 Release 的 Assets**（`agentguard-<tag>.zip`，与 GitHub 附件**同一文件**）；源码见 tag `<tag>`。
     ② 「校验」那行 → 在 GitHub 的 SHA1/大小之后**补一行**内网镜像的 sha256 + 字节数
        （数值从 `docs/releases/assets/README.md` 的表里取，或 sha256sum 现算）：
        内网镜像 sha256：`<sha256>`（<bytes> B，与 GitHub 附件同一文件）
     为什么：内网同事下载的是**镜像文件**，只给 GitHub 的 SHA1 他没法校验；而"内网用中文名 zip"是旧说法（已不成立）。 -->
