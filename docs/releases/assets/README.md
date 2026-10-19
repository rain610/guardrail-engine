# 发布产物（内网镜像）

这里放的是**与 GitHub Release 完全相同的 zip**（同一文件、已核对大小与 sha256），
目的是让**内网同事不必访问 GitHub** 也能下载安装包。

## 为什么放在 git 里
内网 GitLab 实例**没有任何 runner**（实测：Specific/Shared/Group runners 三栏全空，
Shared runners 明确写着 "This GitLab instance does not provide any shared runners yet."），
所以内网 Release **无法自动创建**、也无法挂附件 —— 只能：
1. tag 推到内网（发版时 `git push gitlab v<版本>`）；
2. 网页 `Releases → New release` 选该 tag，正文粘 `docs/releases/<tag>.md`；
3. **Assets → Links** 指向本目录的文件（见下表）。

产物 50~90KB 量级，进 git 的成本可忽略；若将来包体积变大（含依赖的 wheel 等），
应改为内网共享盘/对象存储 + Release 只放链接（`docs/packaging/` 里会写明）。

## 前置条件（解压前先确认）

- **Python ≥ 3.11**（实测 3.11/3.12/3.13；低于 3.11 入口会人话报错退出）
- **git**（提交期门禁必需）· **node**（PC/H5 端提交期语法检查必需，缺失会拦提交）
- 完整的前置检查命令见包内《安装说明.md》「前置检查」节（单一来源：`docs/packaging/INSTALL.md`）

## 现有产物

| 版本 | 文件 | 大小 | sha256 |
|---|---|---|---|
| `v0.1.1-dev` | `agentguard-v0.1.1-dev.zip` | 48698 B | `36b30ac0069debdb65f42badabfc95cf8197087f0e62e9220bc082594d9ac8d8` |
| `v0.1.2-dev` | `agentguard-v0.1.2-dev.zip` | 55518 B | `d4a20dfe13266bf8120096f45dbd7148568d032f966c17284786cf3a80ce10b5` |
| `v0.1.3-dev` | `agentguard-v0.1.3-dev.zip` | 91740 B | `de54c3aaf077d683d00da310c328c0f17baa84e9f61e2ad6dd91d8bcb4aeae9a` |
| `v0.1.4-dev` | `agentguard-v0.1.4-dev.zip` | 98583 B | `7c449e16beca549d0d7a3f0037133b14ecad0bc8a4594997955eda2ee4e14a6e` |
| `v0.1.5-dev` | `agentguard-v0.1.5-dev.zip` | 100155 B | `397439dacd77904a38fc77ea13bdfa980e20eb7446d84b0edb85217dad527f87` |
| `v0.1.6-dev` | `agentguard-v0.1.6-dev.zip` | 102321 B | `34d15e7c971297f4c80f62398e93b1401df202f88ef97b1126f8020560fcecf8` |
| `v0.1.7-dev` | `agentguard-v0.1.7-dev.zip` | 113437 B | `2415ba068158418ef2231ba2027c1568430e739a0d0e77e3657e7b04896d5f58` |

资产链接（New release 的 Assets → Links 里填）：

> ⚠️ 指向 **`main`** 而不是 tag —— 因为这些镜像文件是在**打 tag 之后**才提交进仓的，
> 用 `-/raw/<tag>/…` 会 404（tag 指向的旧提交里还没有这些文件）。
> 要"不可变"链接时可改用具体 commit SHA（`-/raw/<sha>/…`），但那串哈希对同事不友好。

```text
https://resources.ehsway.com:8929/developer/agentguard/-/raw/main/docs/releases/assets/agentguard-<tag>.zip
```
