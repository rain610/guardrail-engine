---
name: eagle-rmc-pc-page-dev
description: gxgk PC Vue 页面开发规范——5种页面骨架模板、表单/抽屉写法、eagle-* 组件速查、接口约定、常见坑。触发:开发/修改 PC Vue 页面或用户说"参照 X 页面做"。基于真实代码蒸馏(2026-08-07)。
---

# Eagle RMC PC Vue(gxgk)页面开发规范

> 来源:gxgk PC Vue 项目真实页面蒸馏,非想象规范。目标:页面照模板抄、组件用对组合、少返工。

## 正文在哪（唯一住处）

**本文件只是路由，不含正文。** 全部正文在引擎仓的 `golden-patterns/pc/` 下：

```
<HARNESS>/golden-patterns/pc/
```

这样做的理由：正文必须**只有一份**且**在版本控制里**（原来那份住在 `~/.skills-manager/`，
不是 git 仓、不随 harness 分发，改一处别人收不到）。详细判据见
`<HARNESS>/golden-patterns/templates/00_知识分层设计.md`。

## 核心原则

1. **先找同类型标本页，再动手**——项目里有现成页面就别发明新结构
2. **`eagle-page` 是列表页心脏**：自带搜索/分页/列设置/刷新，方法 `onSearch` / `onRefresh` / `handleDelete` / `handleShowAll`
3. **页面骨架只有 5 种**，对号入座（速查表见 `pc_page_patterns.md`）；不确定用哪种就问或找标本

## 详细文档索引（需要什么读什么，别一次全读）

| 需要什么 | 打开 |
|---|---|
| **页面开发 8 步 SOP**（类型判定→套骨架→接口→条件→列→按钮→抽屉→验证） | `pc/pc_dev_sop.md` |
| 5 种骨架速查表 + 类型5 自定义列表 | `pc/pc_page_patterns.md` |
| 类型1 带 Tab 列表 / 类型2 简单列表 / 类型4 列表+行删除 的完整模板 + 通用交互 + 列渲染技巧 | `pc/pc_vue_list_page.md` |
| 类型3 多 Tab 容器页（纯壳） | `pc/pc_tab_page.md` |
| 弹窗表单 / 抽屉 / `user-choose` 多选人员 / 提交模式 | `pc/pc_vue_form_drawer.md` |
| `eagle-*` 组件速查 + `request` 接口约定 + 动态路由 | `pc/pc_components_request.md` · `pc/pc_conventions.md` |
| 常见坑（返工教训）+ 禁用手法 ❌ | `pc/pc_page_patterns.md` §5.3 / 常见坑 |

## 标本页（想抄先看这里）

- 带 Tab 列表页：`views/modules/related/qualificationAudit/list.vue`
- 简单列表页：`views/modules/related/relatedPartyType/list.vue`
- 多 Tab 容器页：`views/modules/related/relatedPartySetting/index.vue`
- 列表+行删除：`views/modules/related/relatedPartyAuditConfig/list.vue`
- 消息中心：`views/modules/home/components/myToDo.vue`

## 最低要求（动手前必读）

- 多人字段拆子表、接口数组传输（`pc_page_patterns.md` 常见坑 1）
- CRLF 行尾保护（常见坑 2）
- 改完跑单文件 `npx eslint`（`pc_dev_sop.md` 步骤 8）
- **GET 请求 URL 拼参与 `params` 不得双传**（会导致 401 签名无效，常见坑 10）
