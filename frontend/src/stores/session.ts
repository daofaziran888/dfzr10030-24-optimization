import { defineStore } from 'pinia'

import { apiContext } from '@/api/context'

type Role = 'operator' | 'readonly'

export const useSessionStore = defineStore('session', {
  state: () => ({
    operator: '值班管理员',
    teamCode: 'TEAM-DAY',
    teamName: '白班',
    shiftLabel: '白班 08:00-20:00',
    role: 'operator' as Role,
    scope: '多源数据采集与RPA自动化平台',
  }),
  getters: {
    canOperate: (state) => state.role === 'operator',
    roleLabel: (state) => (state.role === 'readonly' ? '只读用户' : '值班管理员'),
  },
  actions: {
    setTeam(code: string, name: string, shift: string) {
      this.teamCode = code
      this.teamName = name
      this.shiftLabel = shift
      apiContext.teamCode = code
    },
    setRole(role: Role) {
      this.role = role
      apiContext.role = role
    },
  },
})
