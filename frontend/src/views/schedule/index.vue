<template>
  <section class="page" data-module="schedule">
    <header class="page-head">
      <div>
        <h2>采集调度管理</h2>
        <p class="page-desc">
          按团队计划数据快照受控读取：{{ session.teamName }}只看到本归属计划，同一排期范围内结果稳定。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" :disabled="!session.canOperate" @click="openCreate">登记调度计划</button>
        <button class="btn" data-testid="open-handover" type="button" :disabled="!session.canOperate" @click="openHandover">计划移交</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出采集调度清单</button>
      </div>
    </header>

    <div v-if="snapshotMeta" class="snapshot-bar" data-testid="snapshot-bar">
      <span><span class="snap-label">快照</span>{{ snapshotMeta['快照编号'] }}</span>
      <span><span class="snap-label">排期范围</span>{{ snapshotMeta['排期范围'] }}</span>
      <span><span class="snap-label">版本</span>V{{ snapshotMeta['版本'] }}</span>
      <span>
        <span class="snap-label">核对状态</span>
        <span class="tag" :class="snapshotMeta['核对状态'] === '已核对' ? 'ok' : 'warn'">{{ snapshotMeta['核对状态'] }}</span>
      </span>
      <span>
        <span class="snap-label">可用状态</span>
        <span class="tag" :class="availabilityClass">{{ snapshotMeta['可用状态'] }}</span>
      </span>
      <span><span class="snap-label">计划数</span>{{ snapshotMeta['计划数'] }}</span>
      <label>
        <span class="snap-label">旧结果回查</span>
        <select data-testid="history-select" @change="lookupHistory($event)">
          <option value="">选择历史版本</option>
          <option v-for="version in historyVersions" :key="version" :value="version">V{{ version }}</option>
        </select>
      </label>
      <label>
        <input v-model="sharedMode" data-testid="shared-toggle" type="checkbox" @change="reload" />
        共享计划列表（他团队计划打码）
      </label>
    </div>

    <div v-if="snapshotMeta && snapshotMeta['可用状态'] === '不可用'" class="panel" data-testid="unavailable-banner">
      <span class="tag bad">快照不可用</span>
      快照核对结论为不可用，调度动作已暂停，请先在采集日志页完成复核。
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
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>归属团队</th>
          <th>共享标记</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ teamLabel(String(row['归属团队'] ?? '')) }}</td>
          <td>
            <span class="tag" :class="row['共享标记'] === '本团队' ? 'ok' : 'muted'">{{ row['共享标记'] }}</span>
          </td>
          <td class="row-actions">
            <template v-if="row['共享标记'] === '本团队'">
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
            </template>
            <button class="link" type="button" :data-testid="`detail-${row.id}`" @click="openDetail(row)">详情</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 3" class="empty-state">本团队快照内暂无调度计划，可先登记或接收移交</td>
        </tr>
      </tbody>
    </table>

    <div class="panel" data-testid="ownership-changes">
      <h3>归属变更记录（{{ session.teamName }}）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>计划编号</th><th>原团队</th><th>新团队</th><th>变更版本</th><th>变更方式</th><th>操作人</th><th>变更时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="change in changes" :key="String(change['id'])">
            <td>{{ change['计划编号'] }}</td>
            <td>{{ teamLabel(String(change['原团队'])) }}</td>
            <td>{{ teamLabel(String(change['新团队'])) }}</td>
            <td>V{{ change['变更版本'] }}</td>
            <td>{{ change['变更方式'] }}</td>
            <td>{{ change['操作人'] }}</td>
            <td>{{ change['变更时间'] }}</td>
          </tr>
          <tr v-if="!changes.length">
            <td colspan="7" class="empty-state">暂无归属变更记录</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记调度计划（归属{{ session.teamName }}）</h3>
        <label v-for="field in columns" :key="field" class="create-field">
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

    <div v-if="showHandover" class="modal-backdrop" data-testid="handover-modal">
      <form class="modal" @submit.prevent="submitHandover">
        <h3>计划移交（{{ session.teamName }} → 目标团队）</h3>
        <label class="create-field">
          <span>目标团队</span>
          <select v-model="handoverTarget" data-testid="handover-target" required>
            <option value="" disabled>选择目标团队</option>
            <option v-for="team in otherTeams" :key="team.code" :value="team.code">{{ team.name }}</option>
          </select>
        </label>
        <div class="radio-row">
          <label>
            <input v-model="handoverMode" type="radio" value="整体刷新" data-testid="handover-mode-full" />
            整体刷新（快照整体按新归属生效）
          </label>
          <label>
            <input v-model="handoverMode" type="radio" value="逐条撤销" data-testid="handover-mode-item" />
            逐条撤销（计划按条目逐个切换）
          </label>
        </div>
        <div v-if="handoverMode === '逐条撤销'" class="handover-plans" data-testid="handover-plans">
          <label v-for="plan in ownPlans" :key="String(plan.id)">
            <input v-model="handoverPlanIds" type="checkbox" :value="Number(plan.id)" />
            {{ plan['计划编号'] }}（{{ plan['计划状态'] }}）
          </label>
        </div>
        <p class="page-desc">
          基于版本 V{{ snapshotMeta?.['版本'] ?? 0 }} 发起，移交边界一次生效；提交后本团队立即失去这些计划的详情读取权。
        </p>
        <div class="page-actions">
          <button class="btn" type="button" @click="showHandover = false">取消</button>
          <button class="btn primary" data-testid="submit-handover" type="submit" :disabled="handovering">
            {{ handovering ? '移交中…' : '确认移交' }}
          </button>
        </div>
      </form>
    </div>

    <div v-if="detailEntry" class="modal-backdrop" data-testid="detail-modal">
      <div class="modal">
        <h3>计划详情（{{ detailEntry['计划编号'] }}）</h3>
        <dl class="detail-grid">
          <template v-for="field in detailFields" :key="field">
            <dt>{{ field }}</dt>
            <dd>{{ detailEntry[field] ?? '—' }}</dd>
          </template>
        </dl>
        <div class="page-actions">
          <button class="btn" type="button" @click="detailEntry = null">关闭</button>
        </div>
      </div>
    </div>

    <div v-if="historySnapshot" class="modal-backdrop" data-testid="history-modal">
      <div class="modal">
        <h3>旧结果回查：{{ historySnapshot['快照编号'] }}</h3>
        <p class="page-desc">
          版本 V{{ historySnapshot['版本'] }} · {{ historySnapshot['生效状态'] }} ·
          核对状态 {{ historySnapshot['核对状态'] }} · 可用状态 {{ historySnapshot['可用状态'] }}
        </p>
        <table class="data-table">
          <thead>
            <tr><th>计划编号</th><th>计划状态</th></tr>
          </thead>
          <tbody>
            <tr v-for="item in historySnapshot['计划条目']" :key="String(item['id'])">
              <td>{{ item['计划编号'] }}</td>
              <td>{{ item['计划状态'] }}</td>
            </tr>
          </tbody>
        </table>
        <div class="page-actions">
          <button class="btn" type="button" @click="historySnapshot = null">关闭</button>
        </div>
      </div>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条采集调度记录（{{ session.teamName }}快照口径）</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-else-if="noticeMessage" class="ok-text">{{ noticeMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import { readError, request } from '@/api/client'
import { useSessionStore } from '@/stores/session'

type Row = Record<string, string | number | null>
type SnapshotMeta = Record<string, string | number | string[]>
type SnapshotDoc = Record<string, unknown> & { 计划条目: Array<Record<string, string | number>> }

const ENDPOINT = '/api/schedule'
const columns = ["计划编号", "关联任务", "调度周期", "开始时间", "结束时间", "依赖任务", "失败策略", "计划状态"]
const actions = ["确认排期", "启动调度", "暂停调度"]
const statuses = ["草稿", "已排期", "运行中", "已完成", "已暂停"]
const metricNames = ["运行中计划", "已排期计划", "待排期计划"]
const detailFields = [...columns, '归属团队', '归属版本', '排期范围']

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
const snapshotMeta = ref<SnapshotMeta | null>(null)
const sharedMode = ref(false)
const changes = ref<Array<Record<string, string | number>>>([])
const teams = ref<Array<{ code: string; name: string }>>([])
const showHandover = ref(false)
const handovering = ref(false)
const handoverTarget = ref('')
const handoverMode = ref<'整体刷新' | '逐条撤销'>('整体刷新')
const handoverPlanIds = ref<number[]>([])
const detailEntry = ref<Row | null>(null)
const historySnapshot = ref<SnapshotDoc | null>(null)

const stats = computed(() => [
  { label: metricNames[0], value: rows.value.filter((row) => row.status === statuses[2]).length },
  { label: metricNames[1], value: rows.value.filter((row) => row.status === statuses[1]).length },
  { label: metricNames[2], value: rows.value.filter((row) => row.status === statuses[0]).length },
])

const availabilityClass = computed(() => {
  const value = snapshotMeta.value?.['可用状态']
  if (value === '可用') return 'ok'
  if (value === '不可用') return 'bad'
  return 'warn'
})

const historyVersions = computed(() => {
  const versions = snapshotMeta.value?.['历史版本']
  const current = Number(snapshotMeta.value?.['版本'] ?? 0)
  return Array.isArray(versions) ? versions.filter((version) => Number(version) !== current) : []
})

const ownPlans = computed(() => rows.value.filter((row) => row['共享标记'] === '本团队'))
const otherTeams = computed(() => teams.value.filter((team) => team.code !== session.teamCode))

function teamLabel(code: string) {
  return teams.value.find((team) => team.code === code)?.name ?? code
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
    link.download = 'schedule.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(columns.map((field) => [field, '']))
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
    noticeMessage.value = payload.message || '调度计划已登记'
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || payload.detail || '采集调度动作未生效')
    }
    noticeMessage.value = payload.message || `调度计划已${action}`
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集调度操作失败'
  }
}

function openHandover() {
  handoverTarget.value = otherTeams.value[0]?.code ?? ''
  handoverMode.value = '整体刷新'
  handoverPlanIds.value = []
  errorMessage.value = ''
  showHandover.value = true
}

async function submitHandover() {
  handovering.value = true
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/handover`, {
      method: 'POST',
      body: JSON.stringify({
        to_team: handoverTarget.value,
        mode: handoverMode.value,
        plan_ids: handoverMode.value === '逐条撤销' ? handoverPlanIds.value : [],
        base_version: Number(snapshotMeta.value?.['版本'] ?? 0),
      }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || payload.detail || '移交未生效')
    }
    showHandover.value = false
    noticeMessage.value = payload.message || '移交完成'
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '移交操作失败'
  } finally {
    handovering.value = false
  }
}

async function openDetail(row: Row) {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) {
      throw await readError(response, '详情读取失败')
    }
    detailEntry.value = await response.json()
  } catch (error) {
    // 归属变更后旧团队继续看详情会在这里拿到冲突说明
    errorMessage.value = error instanceof Error ? error.message : '详情读取失败'
  }
}

async function lookupHistory(event: Event) {
  const version = (event.target as HTMLSelectElement).value
  if (!version) return
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/snapshot?version=${version}`)
    if (!response.ok) {
      throw await readError(response, '历史快照读取失败')
    }
    const payload = await response.json()
    historySnapshot.value = payload.snapshot as SnapshotDoc
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '历史快照读取失败'
  } finally {
    ;(event.target as HTMLSelectElement).value = ''
  }
}

async function loadChanges() {
  try {
    const response = await request(`${ENDPOINT}/ownership_changes`)
    if (response.ok) {
      const payload = await response.json()
      changes.value = payload.items ?? []
    }
  } catch {
    changes.value = []
  }
}

async function loadTeams() {
  try {
    const response = await request(`${ENDPOINT}/teams`)
    if (response.ok) {
      const payload = await response.json()
      teams.value = (payload.items ?? []).map((item: Record<string, string>) => ({
        code: String(item['团队编号']),
        name: String(item['团队名称']),
      }))
    }
  } catch {
    teams.value = []
  }
}

async function reload() {
  errorMessage.value = ''
  noticeMessage.value = ''
  const params = new URLSearchParams(filters.value as Record<string, string>)
  if (sharedMode.value) params.set('shared', 'true')
  try {
    const response = await request(`${ENDPOINT}?${params.toString()}`)
    if (!response.ok) {
      throw new Error('调度计划列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    snapshotMeta.value = payload.snapshot ?? null
    await loadChanges()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集调度列表读取失败'
  }
}

watch(() => session.teamCode, () => {
  sharedMode.value = false
  void reload()
})

onMounted(async () => {
  await loadTeams()
  await reload()
})
</script>
