---
name: eagle-rmc-scripts
description: "在 Eagle RMC 任一客户×三端仓库里生成脚本时用：临时脚本（探测/验证/建表/数据修复/一次性处理）统一放 <仓>/docs/scripts/，禁止散落仓根或业务源码目录。触发词：写个脚本、生成脚本、建表脚本、数据修复脚本、探测脚本、跑个 Python、.sql 脚本放哪、脚本放哪。"
---

# Eagle RMC 临时脚本放置规范（跨客户×三端通用）

> 本文件**正文内联**（不写「本文件只是路由」）。理由：2026-10-15 实测，纯路由式 SKILL.md
> 会出现「agent 加载了 skill 但没去读正文」→ 规范等于没生效。关键约束必须在这一屏内看完。

适用于多客户（gxgk / hy / hy-hzy / ntrx / keji / zhongdj / c712 / zhongxin …）×
三端（PC Vue / H5 uniapp / .NET 后端）仓库。

## 一、核心规则（一句话）

**AI 生成的临时脚本，一律放 `<仓>/docs/scripts/`，禁止放仓根、禁止放业务源码目录。**

| 脚本类型 | 落点 |
|---|---|
| 探测/验证（查库看数据、对比环境） | `<仓>/docs/scripts/` |
| 建表 / 初始化数据 | `<仓>/docs/scripts/` |
| 一次性数据修复 / 回填 / 去重 | `<仓>/docs/scripts/` |
| 备份 SQL | `<仓>/docs/scripts/backup/` |

## 二、命名

```
<环境>_<主题>_<动作>_<YYYYMMDD>.<ext>
```

- 环境：`dev183` / `prod192` / 目标库标识（**写清是哪个库，因为 183 和 192 都连得上**）
- 主题：`org_rule` / `menu` / `user_post`
- 动作：`setup`（建表/注册）/ `probe`（探测）/ `verify`（验证）/ `fix`（修复）/ `backfill`（回填）

正例（gxgk 已落地的真实命名）：

```
dev183_org_rule_setup_20261015.py
dev183_menu_setup_20261015.py
dev183_data_origin_probe_20261015.py
```

反例❌：`api_test.py`、`verify_db.py`、`cleanup.py`、`test1.py`、`新建文件夹/a.py`
—— 看不出环境、看不出日期、三个月后没人敢删也没人敢跑。

## 三、脚本本身的要求（比放哪更重要）

1. **幂等**：可重复执行，重跑不产生重复数据（建表先判存在，插入先查重）。
2. **默认 dry-run**：只打印「将要做什么」，加 `--apply` 参数才真执行。
   改数据的脚本不做这层保护，等于把生产库交给一次回车。
3. **连接配置集中在文件头部**（如 `CONF = {...}`），改指向生产只改一处，不要在逻辑里散落 host。
4. **输出面向人**：关键步骤打印中文说明与行数统计，别只 print 一个 dict。
5. **脚本尾部说明如何指向生产**：写清「改 CONF 的 host 即可，脚本幂等可重复执行」。

## 四、例外（这些**不**归入 docs，别误伤）

| 情形 | 落点 | 说明 |
|---|---|---|
| **上线脚本**（随版本发布） | 项目既有约定目录，如 `RMC/Eagle.RMC.Web/wwwroot/VersionLog/<版本>/` | 这是发版流程的一部分，挪走会破坏上线 |
| 迁移脚本（EF Migration） | `Migrations/` | 框架约定 |
| 项目自带构建/部署脚本 | 原位置 | 不在本规范范围 |

判断口径：**是不是「这次任务临时写、用完就归档」的脚本**。是 → docs/scripts；是产品的一部分 → 留原地。

## 五、已知乱掉的现场（引以为戒）

2026-10-15 实测，这些是**已经散落**的（hy 系列，待清理）：

```
eagle-rmc-back-net-hy-hzy/api_test.py
eagle-rmc-back-net-hy-hzy/auto_test.py
eagle-rmc-back-net-hy-hzy/cleanup.py
eagle-rmc-back-net-hy-hzy/query_db.py
eagle-rmc-back-net-hy-hzy/test_import_two_depts.py
eagle-rmc-back-net-hy-hzy/verify_db.py
eagle-rmc-back-net-hy-hzy/RMCHY/Sql_Info_QRCodePath_InsertMKY.sql
eagle-rmc-mobile-net-hy-hzy/planEmergency_menu.sql
```

正面样板：**`eaglermc_back_net-gxgk/docs/scripts/`**（98 个文件全部归位，含 `backup/` 子目录）。

## 六、动手前 3 秒自查

- [ ] 路径是 `<仓>/docs/scripts/`？（不是仓根、不是 `RMC/`、`src/`）
- [ ] 文件名带环境 + 日期？
- [ ] 默认 dry-run、`--apply` 才执行？
- [ ] 幂等吗？连生产库重跑一遍会出事吗？
