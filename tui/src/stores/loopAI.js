import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { useAppConfig } from './appConfig.js'
import { fetchConfig } from '../services/config.js'
import { createTask, deleteTask, fetchTasks, updateTask } from '../services/task.js'
import { fetchTaskSession, fetchTaskStatus, submitTaskQuery } from '../services/starter.js'
import { buildNodeCards } from '../lib/nodes.js'
import { normalizeTask, normalizeTaskList } from '../lib/tasks.js'

export const useLoopAI = defineStore('loopAI', () => {
  const appConfig = useAppConfig()

  const config = ref(null)
  const tasks = ref([])
  const selectedTaskIndex = ref(0)
  const currentTask = ref(null)
  const taskStatus = ref(null)
  const session = ref(null)
  const inputBuffer = ref('')
  const scrollOffset = ref(0)
  const nodeScrollOffset = ref(0)
  const nowActivePane = ref('state')
  const nodeStateScrollOffset = ref(0)
  const nodeCustomScrollOffset = ref(0)
  const toolScrollOffset = ref(0)
  const assistantScrollOffset = ref(0)
  const loading = ref(false)
  const toast = ref('Loading LoopAI TUI...')
  const lastRefreshAt = ref(null)

  function local(text) {
    return appConfig.local(text)
  }

  function ensureTaskSelection() {
    if (!tasks.value.length) {
      selectedTaskIndex.value = 0
      currentTask.value = null
      return
    }
    const safeIndex = Number.isFinite(selectedTaskIndex.value) ? selectedTaskIndex.value : 0
    selectedTaskIndex.value = Math.min(Math.max(safeIndex, 0), tasks.value.length - 1)
    currentTask.value = tasks.value[selectedTaskIndex.value] || null
  }

  async function bootstrap() {
    loading.value = true
    toast.value = local('Loading LoopAI TUI...')
    const [configResp, tasksResp] = await Promise.all([fetchConfig(), fetchTasks()])
    if (configResp.code === 200) {
      config.value = configResp.data
      appConfig.setLanguageFromConfig(configResp.data)
    }
    if (tasksResp.code !== 200) throw new Error(local('Failed to load tasks'))
    tasks.value = normalizeTaskList(tasksResp.data)
    ensureTaskSelection()
    await refreshCurrent(true)
    loading.value = false
    toast.value = local('Ready')
  }

  async function refreshTasks(keepTaskId = true) {
    const currentTaskId = keepTaskId ? currentTask.value?.task_id : null
    const resp = await fetchTasks()
    if (resp.code !== 200) throw new Error(local('Failed to load tasks'))
    tasks.value = normalizeTaskList(resp.data)
    if (currentTaskId) {
      const index = tasks.value.findIndex((item) => item.task_id === currentTaskId)
      selectedTaskIndex.value = index >= 0 ? index : 0
    } else {
      selectedTaskIndex.value = 0
    }
    ensureTaskSelection()
  }

  async function refreshCurrent(silent = false) {
    ensureTaskSelection()
    if (!currentTask.value?.task_id) {
      taskStatus.value = null
      session.value = null
      lastRefreshAt.value = new Date().toLocaleString('zh-CN', { hour12: false })
      scrollOffset.value = 0
      nodeScrollOffset.value = 0
      nodeStateScrollOffset.value = 0
      nodeCustomScrollOffset.value = 0
      toolScrollOffset.value = 1000000
      assistantScrollOffset.value = 1000000
      nowActivePane.value = 'state'
      return
    }
    if (!silent) {
      loading.value = true
      toast.value = `${local('Refreshing')} ${currentTask.value.name || currentTask.value.task_id || '-'}...`
    }
    const [statusResp, sessionResp] = await Promise.all([
      fetchTaskStatus(currentTask.value.task_id),
      fetchTaskSession(currentTask.value.task_id)
    ])
    taskStatus.value = statusResp.code === 200 ? statusResp.data : null
    session.value = sessionResp.code === 200 ? sessionResp.data : null
    lastRefreshAt.value = new Date().toLocaleString('zh-CN', { hour12: false })
    loading.value = false
    toast.value = local('Synced')
  }

  async function selectTaskByDelta(delta) {
    if (!tasks.value.length) return
    selectedTaskIndex.value = Math.min(tasks.value.length - 1, Math.max(0, selectedTaskIndex.value + delta))
    ensureTaskSelection()
  }

  async function activateSelectedTask() {
    ensureTaskSelection()
    await refreshCurrent()
    appConfig.setPage('now')
    scrollOffset.value = 0
    nodeScrollOffset.value = 0
    nodeStateScrollOffset.value = 0
    nodeCustomScrollOffset.value = 0
    toolScrollOffset.value = 1000000
    assistantScrollOffset.value = 1000000
    nowActivePane.value = 'state'
  }

  async function createNewTask(name) {
    if (!name?.trim()) throw new Error(local('Name cannot be empty'))
    const resp = await createTask({
      name: name.trim(),
      config: JSON.stringify(config.value || { system: {}, states: {} }),
      state: ''
    })
    if (resp.code !== 200) throw new Error(resp.message || local('Create task failed'))
    await refreshTasks(false)
    const createdTask = normalizeTask(resp.data)
    const index = tasks.value.findIndex((item) => item.task_id === createdTask?.task_id)
    selectedTaskIndex.value = index >= 0 ? index : 0
    ensureTaskSelection()
    await refreshCurrent(true)
    appConfig.setPage('now')
    scrollOffset.value = 0
    nodeScrollOffset.value = 0
    nodeStateScrollOffset.value = 0
    nodeCustomScrollOffset.value = 0
    toolScrollOffset.value = 1000000
    assistantScrollOffset.value = 1000000
    nowActivePane.value = 'state'
    toast.value = local('Task created')
  }

  async function renameCurrentTask(name) {
    if (!currentTask.value?.id) throw new Error(local('No task selected'))
    if (!name?.trim()) throw new Error(local('Name cannot be empty'))
    const resp = await updateTask({ id: currentTask.value.id, name: name.trim() })
    if (resp.code !== 200) throw new Error(resp.message || local('Rename task failed'))
    await refreshTasks(true)
    await refreshCurrent(true)
    toast.value = local('Task renamed')
  }

  async function deleteCurrentTask() {
    if (!currentTask.value?.id) throw new Error(local('No task selected'))
    const resp = await deleteTask(currentTask.value.id)
    if (resp.code !== 200) throw new Error(resp.message || local('Delete task failed'))
    await refreshTasks(false)
    await refreshCurrent(true)
    appConfig.setPage('tasks')
    toast.value = local('Task deleted')
  }

  async function sendQuery(query) {
    if (!currentTask.value?.task_id) throw new Error(local('No task selected'))
    if (!query?.trim()) return
    loading.value = true
    toast.value = `${local('Refreshing')}...`
    const resp = await submitTaskQuery({
      prompt: query.trim(),
      workspace: '',
      session_id: currentTask.value.task_id
    })
    loading.value = false
    if (resp.code !== 200) throw new Error(resp.message || local('Unknown'))
    toast.value = local('submitted')
    await refreshCurrent(true)
  }

  async function runSlashCommand(commandLine) {
    const raw = commandLine.trim()
    const [command, ...args] = raw.split(' ')
    const rest = args.join(' ').trim()

    if (command === '/home') {
      appConfig.setPage('home')
      toast.value = local('Synced')
      return true
    }
    if (command === '/tasks') {
      await refreshTasks(true)
      ensureTaskSelection()
      appConfig.setPage('tasks')
      toast.value = local('Synced')
      return true
    }
    if (command === '/now') {
      await refreshTasks(true)
      ensureTaskSelection()
      appConfig.setPage('now')
      scrollOffset.value = 0
      nodeScrollOffset.value = 0
      nodeStateScrollOffset.value = 0
      nodeCustomScrollOffset.value = 0
      toolScrollOffset.value = 1000000
      assistantScrollOffset.value = 1000000
      nowActivePane.value = 'state'
      if (currentTask.value?.task_id) await refreshCurrent(true)
      toast.value = currentTask.value?.task_id ? local('Synced') : local('No task selected')
      return true
    }
    if (command === '/refresh') {
      await refreshTasks(true)
      await refreshCurrent()
      return true
    }
    if (command === '/new') {
      await createNewTask(rest)
      return true
    }
    if (command === '/rename') {
      await renameCurrentTask(rest)
      return true
    }
    if (command === '/delete') {
      await deleteCurrentTask()
      return true
    }
    if (command === '/quit') {
      return 'quit'
    }
    throw new Error(`${local('Unknown')}: ${command}`)
  }

  async function submitInput() {
    const value = inputBuffer.value.trim()
    if (!value) return null
    inputBuffer.value = ''
    if (value.startsWith('/')) {
      return await runSlashCommand(value)
    }
    if (appConfig.page !== 'now') throw new Error(local('Unknown'))
    await sendQuery(value)
    return true
  }

  function appendInput(chars) {
    inputBuffer.value += chars
  }

  function backspaceInput() {
    inputBuffer.value = inputBuffer.value.slice(0, -1)
  }

  function clearInput() {
    inputBuffer.value = ''
  }

  function setToast(message) {
    toast.value = message
  }

  function scrollBy(delta) {
    scrollOffset.value = Math.max(0, scrollOffset.value + delta)
  }

  function resetScroll() {
    scrollOffset.value = 0
  }

  function scrollNodeBoardBy(delta) {
    nodeScrollOffset.value = Math.max(0, nodeScrollOffset.value + delta)
    nodeStateScrollOffset.value = 0
    nodeCustomScrollOffset.value = 0
  }

  function resetNodeBoardScroll() {
    nodeScrollOffset.value = 0
  }

  function cycleNowPane() {
    const panes = ['state', 'custom', 'tool', 'assistant']
    const current = panes.indexOf(nowActivePane.value)
    nowActivePane.value = panes[(current + 1) % panes.length]
  }

  function scrollNowPaneBy(delta) {
    if (nowActivePane.value === 'state') nodeStateScrollOffset.value = Math.max(0, nodeStateScrollOffset.value + delta)
    else if (nowActivePane.value === 'custom') nodeCustomScrollOffset.value = Math.max(0, nodeCustomScrollOffset.value + delta)
    else if (nowActivePane.value === 'tool') toolScrollOffset.value = Math.max(0, toolScrollOffset.value + delta)
    else assistantScrollOffset.value = Math.max(0, assistantScrollOffset.value + delta)
  }

  return {
    config,
    tasks,
    selectedTaskIndex,
    currentTask,
    taskStatus,
    session,
    inputBuffer,
    scrollOffset,
    nodeScrollOffset,
    nowActivePane,
    nodeStateScrollOffset,
    nodeCustomScrollOffset,
    toolScrollOffset,
    assistantScrollOffset,
    loading,
    toast,
    lastRefreshAt,
    page: computed(() => appConfig.page),
    nodeCards: computed(() => buildNodeCards(taskStatus.value || {})),
    conversation: computed(() => Array.isArray(session.value?.conversation) ? session.value.conversation : []),
    sessionEvents: computed(() => Array.isArray(session.value?.events) ? session.value.events : []),
    sessionStatus: computed(() => session.value?.status || 'not_started'),
    bootstrap,
    refreshTasks,
    refreshCurrent,
    selectTaskByDelta,
    activateSelectedTask,
    createNewTask,
    renameCurrentTask,
    deleteCurrentTask,
    runSlashCommand,
    submitInput,
    appendInput,
    backspaceInput,
    clearInput,
    setToast,
    scrollBy,
    resetScroll,
    scrollNodeBoardBy,
    resetNodeBoardScroll,
    cycleNowPane,
    scrollNowPaneBy,
    ensureTaskSelection,
    local
  }
})
