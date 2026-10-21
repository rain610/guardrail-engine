# 列表页：后端分页 + 筛选 + 批量 + 行操作

> 适用：带查询条件、分页、行操作或批量操作的 CRUD 列表页。
> 参考实现：`golden-patterns/reference/web/task-list.vue`
> 对接规则：无结构规则；调试残留会被 `NO-CONSOLE` 拦住。

## 适用

- 新增一个「查询 + 表格 + 分页 + 操作」的页面。
- 把旧页面的前端全量拉取改成分页。
- 不适用：详情页、纯展示大屏、树形结构页（另建范式）。

## 骨架（可照抄）

```vue
<template>
  <div class="task-page">
    <el-form :inline="true" :model="query" @submit.prevent="search">
      <el-form-item label="关键字">
        <el-input v-model="query.keyword" clearable @keyup.enter="search" />
      </el-form-item>
      <el-form-item label="状态">
        <el-select v-model="query.status" clearable placeholder="全部">
          <el-option v-for="item in STATUS_OPTIONS" :key="item.value"
            :label="item.label" :value="item.value" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" native-type="submit">查询</el-button>
        <el-button @click="reset">重置</el-button>
      </el-form-item>
    </el-form>

    <el-button type="primary" @click="openCreate">新增任务</el-button>
    <el-button type="danger" plain :disabled="selectedIds.length === 0"
      @click="removeSelected">批量删除</el-button>

    <el-table v-loading="loading" :data="rows" row-key="id"
      empty-text="暂无任务" @selection-change="onSelectionChange">
      <el-table-column type="selection" width="48" />
      <el-table-column prop="title" label="标题" min-width="220" show-overflow-tooltip />
      <el-table-column prop="owner" label="负责人" width="120" />
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="STATUS_META[row.status]?.tag ?? 'info'">
            {{ STATUS_META[row.status]?.label ?? row.status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
          <el-button v-if="row.status === 'Draft'" link type="success"
            @click="changeStatus(row, 'InProgress')">开始</el-button>
          <el-dropdown trigger="click" @command="(cmd) => onRowCommand(cmd, row)">
            <el-button link type="primary">更多</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="delete">删除</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination v-model:current-page="query.page" v-model:page-size="query.pageSize"
      :total="total" :page-sizes="[10, 20, 50]"
      layout="total, sizes, prev, pager, next, jumper"
      @current-change="fetchList" @size-change="search" />

    <task-form ref="formRef" @saved="fetchList" />
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue"
import { ElMessage, ElMessageBox } from "element-plus"
import request from "@/utils/request"
import TaskForm from "./task-form.vue"

const STATUS_META = {
  Draft: { label: "草稿", tag: "info" },
  InProgress: { label: "进行中", tag: "warning" },
  Done: { label: "已完成", tag: "success" },
}
const STATUS_OPTIONS = Object.entries(STATUS_META).map(([value, meta]) => ({
  value,
  label: meta.label,
}))
const PRIORITY_LABEL = { Low: "低", Normal: "中", High: "高" }

const rows = ref([])
const total = ref(0)
const loading = ref(false)
const selectedIds = ref([])
const formRef = ref()
const query = reactive({ keyword: "", status: "", page: 1, pageSize: 20 })

const fetchList = async () => {
  loading.value = true
  try {
    const data = await request.get("/tasks", { params: query })
    rows.value = data.items
    total.value = data.total
  } catch {
    // 提示由 request 拦截器统一处理；保留当前列表，避免网络抖动清空页面
  } finally {
    loading.value = false
  }
}

const search = () => {
  query.page = 1
  fetchList()
}

const reset = () => {
  query.keyword = ""
  query.status = ""
  search()
}

const onSelectionChange = (selection) => {
  selectedIds.value = selection.map((item) => item.id)
}

const openCreate = () => formRef.value.open()
const openEdit = (row) => formRef.value.open(row)

const askDelete = async (message) => {
  const confirmed = await ElMessageBox.confirm(message, "删除确认", {
    type: "warning",
  }).catch(() => false)
  return confirmed !== false
}

const removeOne = async (row) => {
  if (!(await askDelete(`确定删除任务「${row.title}」吗？`))) return
  try {
    await request.delete(`/tasks/${row.id}`)
    ElMessage.success("删除成功")
    fetchList()
  } catch {
    // 提示由 request 拦截器统一处理
  }
}

const removeSelected = async () => {
  if (!(await askDelete(`确定删除选中的 ${selectedIds.value.length} 个任务吗？`))) return
  try {
    await request.post("/tasks/batch-delete", { ids: selectedIds.value })
    selectedIds.value = []
    ElMessage.success("删除成功")
    fetchList()
  } catch {
    // 提示由 request 拦截器统一处理
  }
}

const changeStatus = async (row, status) => {
  try {
    await request.patch(`/tasks/${row.id}/status`, { status })
    ElMessage.success("状态已更新")
    fetchList()
  } catch {
    // 提示由 request 拦截器统一处理
  }
}

const onRowCommand = (command, row) => {
  if (command === "delete") removeOne(row)
}

onMounted(fetchList)
</script>
```

完整可读版本见参考实现文件。

## 照抄要点

1. **分页在后端**。查询参数带 `page / pageSize`，接口返回 `{ items, total }`；禁止 `getAll()` 后在内存里 `slice`。
2. **查询/重置都回到第 1 页**。只改条件不重置页码，用户会停在一个空白的第 5 页。
3. **`loading` 必须 `finally` 复位**。请求失败后按钮一直转圈，比报错更难排查。
4. **失败不清空列表**。网络抖动时保留旧数据，用户还能看到内容；错误提示由请求层统一弹。
5. **删除/批量删除必须二次确认**。取消时用 `.catch(() => false)` 接住 `reject`，否则控制台会多一条未处理异常。
6. **行操作最多 3 个直接按钮**。第 4 个起收进「更多」下拉；按钮一多，列宽和移动端都会被撑爆。
7. **状态文案集中映射**。`STATUS_META` 一处定义 label/tag 颜色，模板里不要散落三元表达式。状态字典由后端统一下发时，优先用字典。
8. **空状态用表格自带的 `empty-text`**。再叠一个 `el-empty` 会出现「表格空行 + 空卡片」两层空状态。

## 常见坑

- ❌ 前端全量拉取后分页。数据一多就卡死，后端索引也白建。
- ❌ `@current-change="search"`，每次翻页又重置回第 1 页。翻页用 `fetchList`，改条件才用 `search`。
- ❌ 批量删除只传当前页的选中项，或者刷新后不清 `selectedIds`。
- ❌ 删除按钮直接执行，没有 `confirm`；误点即删。
- ❌ 用数组下标当 `row-key`。排序/翻页后选中行会错位。
- ❌ 在 `el-table` 外层写 `v-if="rows.length"` 再配 `v-else` 空态，表格的 loading 和列宽逻辑会丢。
- ❌ 状态标签颜色写死 `red/green`，暗色主题下不可读；用组件支持的语义类型。

## 自检

- [ ] 接口请求带 `page / pageSize`，返回 `total`。
- [ ] 查询和重置都会把 `page` 置为 1。
- [ ] 删除/批量删除有二次确认，取消不会产生未处理异常。
- [ ] `loading` 在 `finally` 里复位。
- [ ] 行操作直接按钮不超过 3 个。
- [ ] 空列表有明确文案，不是一片白。
