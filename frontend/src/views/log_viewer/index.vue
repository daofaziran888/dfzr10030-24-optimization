<template>
  <section class="page" data-module="log_viewer">
    <header class="page-head">
      <div>
        <h2>采集日志管理</h2>
        <p class="page-desc">按团队计划快照记录排期操作日志，越权拒绝记录保留在日志列表；快照复核在工作台完成。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" :disabled="!session.canOperate" @click="openCreate">登记日志条目</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出采集日志清单</button>
      </div>
    </header>

    <div class="panel" data-testid="workbench">
      <h3>待审核工作台（团队计划快照复核）</h3>
      <div v-for="item in workbenchItems" :key="String(item['事项编号'])" class="workbench-item" :data-testid="`wb-${item['事项编号']}`">
        <div>
          <div class="wb-summary">{{ item['摘要'] }}</div>
          <div class="wb-meta">
            {{ item['事项编号'] }} · 批次 {{ item['批次号'] }} · 快照版本 V{{ item['快照版本'] }} · {{ item['生成时间'] }}
          </div>
        </div>
        <div class="row-actions">
          <button
            class="btn primary"
            type="button"
            :disabled="!session.canOperate"
            :data-testid="`review-ok-${item['事项编号']}`"
            @click="submitReview(String(item['批次号']), '可用')"
          >
            复核可用
          </button>
          <button
            class="btn"
            type="button"
            :disabled="!session.canOperate"
            :data-testid="`review-bad-${item['事项编号']}`"
            @click="submitReview(String(item['批次号']), '不可用')"
          >
            复核不可用
          </button>
        </div>
      </div>
      <p v-if="!workbenchItems.length" class="empty-state">暂无待审核的快照复核事项</p>
    </div>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <label class="filter-item">
        <span>日志类型</span>
        <select v-model="filters['日志类型']" data-testid="filter-日志类型" @change="reload">
          <option value="">全部类型</option>
          <option v-for="eventType in eventTypes" :key="eventType" :value="eventType">{{ eventType }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>团队</span>
        <select v-model="filters['团队编号']" data-testid="filter-团队编号" @change="reload">
          <option value="">全部团队</option>
          <option value="TEAM-DAY">白班</option>
          <option value="TEAM-NIGHT">夜班</option>
        </select>
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in columns" :key="column">
            <span v-if="column === '日志类型'" class="tag" :class="eventTagClass(String(row[column] ?? ''))">{{ row[column] ?? '—' }}</span>
            <template v-else>{{ row[column] ?? '—' }}</template>
          </td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :disabled="!session.canOperate"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无采集日志数据，可先登记日志条目</td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记日志条目</h3>
        <label v-for="field in createFields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条采集日志记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-else-if="noticeMessage" class="ok-text">{{ noticeMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'
import { useSessionStore } from '@/stores/session'

type Row = Record<string, string | number | null>
type WorkbenchItem = Record<string, string | number>

const ENDPOINT = '/api/log_viewer'
const columns = ["日志编号", "关联任务", "日志级别", "记录时间", "日志内容", "错误堆栈", "处理状态", "处理人", "日志类型"]
const createFields = columns.slice(0, 8)
const actions = ["开始处理", "标记已处理", "忽略日志"]
const statuses = ["未处理", "处理中", "已处理", "已忽略"]
const metricNames = ["未处理日志", "处理中日志", "已处理日志"]
const eventTypes = ["采集运行", "排期操作", "越权拒绝", "归属变更", "快照复核"]

const session = useSessionStore()

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const noticeMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const workbenchItems = ref<WorkbenchItem[]>([])
const stats = computed(() => [
  { label: metricNames[0], value: total.value },
  { label: metricNames[1], value: rows.value.filter((row) => row.status === statuses[1]).length },
  { label: metricNames[2], value: rows.value.filter((row) => row.status === statuses[statuses.length - 1]).length },
])

function eventTagClass(eventType: string) {
  if (eventType === '越权拒绝') return 'bad'
  if (eventType === '排期操作' || eventType === '归属变更') return 'ok'
  if (eventType === '快照复核') return 'warn'
  return 'muted'
}

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value).toString()
  try {
    const response = await request(`${ENDPOINT}/export?${query}`)
    if (!response.ok) throw new Error('导出失败，请稍后重试')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'log_viewer.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(createFields.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST', body: JSON.stringify(createValues.value),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '登记失败')
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('采集日志动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集日志操作失败'
  }
}

async function submitReview(batchNo: string, conclusion: string) {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/review`, {
      method: 'POST',
      body: JSON.stringify({ batch_no: batchNo, conclusion }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || payload.detail || '复核未生效')
    }
    noticeMessage.value = payload.message || '复核完成'
    await Promise.all([loadWorkbench(), reload()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '复核操作失败'
    await loadWorkbench()
  }
}

async function loadWorkbench() {
  try {
    const response = await request(`${ENDPOINT}/workbench`)
    if (response.ok) {
      const payload = await response.json()
      workbenchItems.value = payload.items ?? []
    }
  } catch {
    workbenchItems.value = []
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('日志条目列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集日志列表读取失败'
  }
}

onMounted(async () => {
  await Promise.all([loadWorkbench(), reload()])
})
</script>
