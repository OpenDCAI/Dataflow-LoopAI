import { computed } from 'vue'
import { formatTime } from '../../lib/format.js'

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value))
}

export function useTaskPanel({ tasks, selectedTaskIndex, bodyHeight }) {
  const taskItemHeight = 3
  const tasksPerPage = computed(() => Math.max(1, Math.floor((bodyHeight.value - 4) / taskItemHeight)))
  const taskPageCount = computed(() => Math.max(1, Math.ceil(tasks.value.length / tasksPerPage.value)))
  const taskPage = computed(() => {
    const pageIndex = Math.floor((selectedTaskIndex.value || 0) / tasksPerPage.value)
    return clamp(pageIndex + 1, 1, taskPageCount.value)
  })
  const taskStart = computed(() => (taskPage.value - 1) * tasksPerPage.value)
  const visibleTasks = computed(() =>
    tasks.value.slice(taskStart.value, taskStart.value + tasksPerPage.value).map((task, index) => ({
      task: {
        ...task,
        updatedLabel: formatTime(task.updatedAt)
      },
      actualIndex: taskStart.value + index
    }))
  )

  return {
    taskItemHeight,
    taskPage,
    taskPageCount,
    visibleTasks
  }
}
