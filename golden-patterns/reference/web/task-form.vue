<template>
  <el-dialog
    v-model="visible"
    :title="mode === 'create' ? '新增任务' : '编辑任务'"
    width="560px"
    :close-on-click-modal="false"
    @closed="reset"
  >
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
      <el-form-item label="截止日期" prop="dueDate">
        <el-date-picker v-model="form.dueDate" type="date" value-format="YYYY-MM-DD" />
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
    if (mode.value === "create") {
      await request.post("/tasks", form)
    } else {
      await request.put(`/tasks/${form.id}`, form)
    }
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
