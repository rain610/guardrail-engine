# 表单弹窗：新增/编辑共用一套，校验通过再提交

> 适用：列表页里「新增 / 编辑」弹窗，字段基本一致。
> 参考实现：`golden-patterns/reference/web/task-form.vue`
> 对接规则：无结构规则；调试残留会被 `NO-CONSOLE` 拦住。

## 适用

- 新增和编辑字段相同，只有标题、回填数据、提交接口不同。
- 弹窗由父页面通过 `ref` 打开，保存后通知父页面刷新。
- 不适用：字段差异很大的新增/编辑（拆两个组件，别用一堆 `v-if` 揉在一起）。

## 骨架（可照抄）

父页面：

```vue
<task-form ref="formRef" @saved="fetchList" />

<script setup>
import { ref } from "vue"
const formRef = ref()
const openCreate = () => formRef.value.open()
const openEdit = (row) => formRef.value.open(row)
</script>
```

子组件 `task-form.vue`：

```vue
<template>
  <el-dialog v-model="visible" :title="mode === 'create' ? '新增任务' : '编辑任务'"
    width="560px" :close-on-click-modal="false" @closed="reset">
    <el-form ref="formRef" :model="form" :rules="rules" label-width="88px">
      <el-form-item label="标题" prop="title">
        <el-input v-model.trim="form.title" maxlength="80" show-word-limit />
      </el-form-item>
      <el-form-item label="负责人" prop="owner">
        <el-input v-model.trim="form.owner" maxlength="30" />
      </el-form-item>
      <el-form-item label="优先级" prop="priority">
        <el-radio-group v-model="form.priority">
          <el-radio value="Low">低</el-radio>
          <el-radio value="Normal">中</el-radio>
          <el-radio value="High">高</el-radio>
        </el-radio-group>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button :disabled="submitting" @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="submit">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { reactive, ref } from "vue"
import { ElMessage } from "element-plus"
import request from "@/utils/request"

const emit = defineEmits(["saved"])
const formRef = ref()
const visible = ref(false)
const submitting = ref(false)
const mode = ref("create")

const toForm = (row) => ({
  id: row?.id ?? "",
  title: row?.title ?? "",
  owner: row?.owner ?? "",
  priority: row?.priority ?? "Normal",
  dueDate: row?.dueDate ?? "",
})

const form = reactive(toForm(null))

const rules = {
  title: [{ required: true, message: "请输入标题", trigger: "blur" }],
  owner: [{ required: true, message: "请填写负责人", trigger: "blur" }],
  priority: [{ required: true, message: "请选择优先级", trigger: "change" }],
}

const open = (row) => {
  Object.assign(form, toForm(row))
  mode.value = row ? "edit" : "create"
  visible.value = true
}

const reset = () => {
  Object.assign(form, toForm(null))
  formRef.value?.clearValidate()
}

const submit = async () => {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  submitting.value = true
  try {
    if (mode.value === "create") await request.post("/tasks", form)
    else await request.put(`/tasks/${form.id}`, form)
    ElMessage.success("保存成功")
    visible.value = false
    emit("saved")
  } catch {
    // 错误提示由 request 拦截器统一处理
  } finally {
    submitting.value = false
  }
}

defineExpose({ open })
</script>
```

如果弹窗需要由父组件用 `v-model` 控制显隐，用 `computed` 桥接，不要直接改 prop：

```js
const props = defineProps({ modelValue: Boolean })
const emit = defineEmits(["update:modelValue"])
const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit("update:modelValue", value),
})
```

## 照抄要点

1. **新增/编辑共用一个组件**。用 `mode` 区分标题和提交方法，不要维护两份 90% 相同的表单。
2. **打开时复制字段**。用 `toForm(row)` 只挑需要的字段，别把整行对象直接塞进表单 —— 取消时会把列表行一起改脏。
3. **校验通过再提交**。`validate()` 不通过会 reject，用 `.catch(() => false)` 接住；不要写成 `if (valid)` 然后发现 `valid` 是 `undefined`。
4. **提交中锁按钮**。`submitting` 同时控制保存按钮 loading 和取消按钮 disabled，防重复提交。
5. **关闭后重置**。`@closed="reset"` 清字段和校验状态；否则下次打开会看到上次的红字。
6. **保存成功通知父页面**。`emit("saved")`，由父页面决定刷新当前页还是回到第 1 页；子组件不直接操作列表。
7. **禁用点遮罩关闭**。`:close-on-click-modal="false"`，避免用户填到一半误点空白处丢数据。

## 常见坑

- ❌ 直接 `Object.assign(form, row)`。表单和列表行共享引用，取消/编辑会污染列表。
- ❌ 用两个弹窗组件分别做新增和编辑，改一个字段要改两处。
- ❌ `validate((valid) => { ... })` 里把 `valid` 当布尔值用，但项目封装层只在通过时回调 —— 先确认组件签名，别照搬原生示例。
- ❌ 提交按钮没有 loading，用户连点两次，后端产生两条记录。
- ❌ 关闭弹窗时只清字段不清校验，下一次打开直接一片红。
- ❌ 子组件保存成功后自己调 `fetchList`，父页面和子组件各自持有一份列表状态。

## 自检

- [ ] 新增/编辑只有一套模板和规则。
- [ ] 打开时是字段拷贝，不是行对象引用。
- [ ] 校验失败不会发请求。
- [ ] 提交中按钮不可重复点击。
- [ ] 关闭后字段和校验状态都已重置。
- [ ] 保存成功只通过事件通知父页面。
