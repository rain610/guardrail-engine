# 测试目录

按**被测对象**分组，不按评审批次分组。新测试先在下表找到自己的位置，再按
`test_<被测对象>.py` 命名；同一被测对象的新用例加进已有文件，不另起文件。

| 目录 | 放什么 | 典型被测入口 |
|---|---|---|
| `engine/` | 引擎运行时行为：规则、配置解析、调度、台账、覆盖度、hook 链路、agent 接入 | `checks/`、`harness/`、`integrations/hooks/` |
| `packaging/` | 安装 / 升级 / 卸载 / 轮子 / 发布链路 | `sandbox/upgrade.py`、`sandbox/uninstall.py`、`pyproject.toml`、`.github/workflows/release.yml` |
| `tooling/` | 开发工具自身 | `sandbox/run_tests.py`、`sandbox/mutation_smoke.py`、`sandbox/fix_paths.py` |
| `docs/` | 文档与黄金范式的一致性 | `README*`、`docs/`、`golden-patterns/` |
| `review/` | **历史评审批次的回归锁**（只增不改） | 各轮外部评审留下的端到端锁 |

`_tmpclean.py`（临时目录清理）与 `_engine_copy.py`（工程副本夹具）留在本层，
各分组共用一份；测试文件通过 `sys.path` 注入本层来导入它们。

## 运行

```bash
# 日常：快跑 + 文件级并行
python sandbox/run_tests.py --fast

# 全量（含慢链路），与 CI 同口径
python sandbox/run_tests.py --full

# 只跑一个分组
python sandbox/run_tests.py --tests-dir tests/engine

# 标准 unittest 发现（递归进入各分组）
python -m unittest discover -s tests -v
```

历史评审批次文件继续留在 `review/`：它们记录的是"当时那一版为什么必须这样修"，
不是新测试的模板。新增回归测试放回对应领域目录，并同步更新
`lock-inventory.json` 的模块键（带分组前缀，如 `engine/test_checks.py`）。
