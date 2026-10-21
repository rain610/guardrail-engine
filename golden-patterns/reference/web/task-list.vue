<template>
  <div class="task-page">
    <el-form :inline="true" :model="query" class="task-filter" @submit.prevent="search">
      <el-form-item label="关键字">
        <el-input
          v-model="query.keyword"
          clearable
          placeholder="标题 / 负责人"
          @keyup.enter="search"
        />
      </el-form-item>
      <el-form-item label="状态">
        <el-select v-model="query.status" clearable placeholder="全部">
          <el-option
            v-for="item in STATUS_OPTIONS"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" native-type="submit">查询</el-button>
        <el-button @click="reset">重置</el-button>
      </el-form-item>
    </el-form>

    <div class="task-toolbar">
      <el-button type="primary" @click="openCreate">新增任务</el-button>
      <el-button
        type="danger"
        plain
        :disabled="selectedIds.length === 0"
        @click="removeSelected"
      >
        批量删除{{ selectedIds.length ? `（${selectedIds.length}）` : "" }}
      </el-button>
    </div>

    <el-table
      v-loading="loading"
      :data="rows"
      row-key="id"
      empty-text="暂无任务"
      @selection-change="onSelectionChange"
    >
      <el-table-column type="selection" width="48" />
      <el-table-column prop="title" label="标题" min-width="220" show-overflow-tooltip />
      <el-table-column prop="owner" label="负责人" width="120" />
      <el-table-column label="优先级" width="100">
        <template #default="{ row }">
          {{ PRIORITY_LABEL[row.priority] ?? row.priority }}
        </template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="STATUS_META[row.status]?.tag ?? 'info'">
            {{ STATUS_META[row.status]?.label ?? row.status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="dueDate" label="截止日期" width="120" />
      <el-table-column label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
          <el-button
            v-if="row.status === 'Draft'"
            link
            type="success"
            @click="changeStatus(row, 'InProgress')"
          >
            开始
          </el-button>
          <el-button
            v-else-if="row.status === 'InProgress'"
            link
            type="success"
            @click="changeStatus(row, 'Done')"
          >
            完成
          </el-button>
          <el-dropdown trigger="click" @command="(command) => onRowCommand(command, row)">
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

    <el-pagination
      v-model:current-page="query.page"
      v-model:page-size="query.pageSize"
      class="task-pagination"
      :total="total"
      :page-sizes="[10, 20, 50]"
      layout="total, sizes, prev, pager, next, jumper"
      @current-change="fetchList"
      @size-change="search"
    />

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
    // 错误提示由 request 拦截器统一处理；保留当前列表，避免一次网络抖动清空页面
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
    // 错误提示由 request 拦截器统一处理
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
    // 错误提示由 request 拦截器统一处理
  }
}

const changeStatus = async (row, status) => {
  try {
    await request.patch(`/tasks/${row.id}/status`, { status })
    ElMessage.success("状态已更新")
    fetchList()
  } catch {
    // 错误提示由 request 拦截器统一处理
  }
}

const onRowCommand = (command, row) => {
  if (command === "delete") removeOne(row)
}

onMounted(fetchList)
</script>
