import { computed } from 'vue'
import { useWindowSize } from '@vue-tui/runtime'
import { storeToRefs } from 'pinia'
import { useAppConfig } from '../../stores/appConfig.js'
import { useLoopAI } from '../../stores/loopAI.js'

export function useTasksView() {
  const appConfig = useAppConfig()
  const loopAI = useLoopAI()
  const { tasks, selectedTaskIndex } = storeToRefs(loopAI)
  const windowSize = useWindowSize()

  const rows = computed(() => windowSize.rows.value || 40)
  const pageSize = computed(() => Math.max(1, Math.floor((rows.value - 12) / 4)))
  const taskCount = computed(() => Array.isArray(tasks.value) ? tasks.value.length : 0)
  const pageCount = computed(() => Math.max(1, Math.ceil(taskCount.value / pageSize.value)))
  const currentPage = computed(() => {
    if (!taskCount.value) return 1
    const safeIndex = Number.isFinite(selectedTaskIndex.value) ? selectedTaskIndex.value : 0
    return Math.floor(safeIndex / pageSize.value) + 1
  })
  const visibleTasks = computed(() => {
    const safeIndex = Number.isFinite(selectedTaskIndex.value) ? selectedTaskIndex.value : 0
    const start = Math.floor(safeIndex / pageSize.value) * pageSize.value
    const source = Array.isArray(tasks.value) ? tasks.value : []
    return source.slice(start, start + pageSize.value).map((task, index) => ({
      task,
      actualIndex: start + index
    }))
  })
  const title = computed(() => `${appConfig.local('Tasks')} (${taskCount.value})`)
  const footerHint = computed(() => `${appConfig.local('page')} ${currentPage.value}/${pageCount.value}`)

  return {
    title,
    pageSize,
    pageCount,
    currentPage,
    visibleTasks,
    footerHint
  }
}
