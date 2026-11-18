<template>
  <div class="app-shell">
    <aside class="app-side">
      <h1 class="app-title">多源数据采集与RPA自动化平台</h1>
      <nav class="nav-list">
        <RouterLink v-for="item in navItems" :key="item.path" :to="item.path" class="nav-item">
          {{ item.label }}
        </RouterLink>
      </nav>
    </aside>
    <main class="app-main">
      <header class="app-head">
        <span class="head-desc">面向网页抓取、API接入、增量采集、RPA流程编排、数据校验与采集监控的数据工程平台。</span>
        <div class="head-session">
          <span class="head-user">当前值班：{{ store.operator }} · {{ store.shiftLabel }}</span>
          <div class="switch-group" data-testid="team-switch">
            <button
              v-for="team in teams"
              :key="team.code"
              class="switch-btn"
              :class="{ active: store.teamCode === team.code }"
              type="button"
              @click="switchTeam(team.code)"
            >
              {{ team.name }}
            </button>
          </div>
          <div class="switch-group" data-testid="role-switch">
            <button
              class="switch-btn"
              :class="{ active: store.role === 'operator' }"
              type="button"
              @click="store.setRole('operator')"
            >
              值班管理员
            </button>
            <button
              class="switch-btn"
              :class="{ active: store.role === 'readonly' }"
              type="button"
              @click="store.setRole('readonly')"
            >
              只读用户
            </button>
          </div>
        </div>
      </header>
      <RouterView />
    </main>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { fetchJson } from '@/api/client'
import { useSessionStore } from '@/stores/session'

const store = useSessionStore()

const teams = ref([
  { code: 'TEAM-DAY', name: '白班', shift: '白班 08:00-20:00' },
  { code: 'TEAM-NIGHT', name: '夜班', shift: '夜班 20:00-08:00' },
])

function switchTeam(code: string) {
  const team = teams.value.find((item) => item.code === code)
  if (team) {
    store.setTeam(team.code, team.name, team.shift)
  }
}

onMounted(async () => {
  try {
    const payload = await fetchJson<{ items: Array<Record<string, string>> }>('/api/schedule/teams')
    const loaded = payload.items.map((item) => ({
      code: String(item['团队编号']),
      name: String(item['团队名称']),
      shift: `${String(item['团队名称'])} ${String(item['排班时段'])}`,
    }))
    if (loaded.length) {
      teams.value = loaded
      const current = loaded.find((item) => item.code === store.teamCode) ?? loaded[0]
      store.setTeam(current.code, current.name, current.shift)
    }
  } catch {
    // 团队排期数据拉取失败时保留内置白班/夜班，页面仍可使用
  }
})

const navItems = [{ label: "运营概览", path: "/" }, { label: "数据源配置", path: "/datasource" }, { label: "网页采集规则", path: "/crawler" }, { label: "API接入管理", path: "/apiclient" }, { label: "增量采集", path: "/incremental" }, { label: "RPA任务编排", path: "/robot_task" }, { label: "数据接入通道", path: "/ingestion" }, { label: "格式解析器", path: "/parser" }, { label: "采集结果校验", path: "/validator" }, { label: "采集去重", path: "/dedup" }, { label: "采集调度", path: "/schedule" }, { label: "采集监控", path: "/monitor" }, { label: "采集日志", path: "/log_viewer" }, { label: "凭证管理", path: "/auth" }, { label: "采集转换管道", path: "/transform" }, { label: "数据流转编排", path: "/flow" }, { label: "数据血缘追踪", path: "/lineage" }, { label: "采集报告", path: "/report" }]
</script>
