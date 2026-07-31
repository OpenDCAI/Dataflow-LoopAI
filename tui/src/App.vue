<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { TView, useLayout } from '@simon_he/vue-tui/vue'
import HomePanel from './components/home/HomePanel.vue'
import AppFooter from './components/layout/AppFooter.vue'
import AppHeader from './components/layout/AppHeader.vue'
import NodeBoard from './components/now/NodeBoard.vue'
import NodeSummary from './components/now/NodeSummary.vue'
import TranscriptPane from './components/now/TranscriptPane.vue'
import TaskList from './components/tasks/TaskList.vue'
import { useNowPanel } from './hooks/now/useNowPanel.js'
import { useTaskPanel } from './hooks/tasks/useTaskPanel.js'
import { useAppConfig } from './stores/appConfig.js'
import { useLoopAI } from './stores/loopAI.js'

const props = defineProps({
  onQuit: {
    type: Function,
    default: null
  }
})

const POLL_MS = 3000
const appConfig = useAppConfig()
const loopAI = useLoopAI()
const layout = useLayout()
const { page } = storeToRefs(appConfig)
const {
  tasks,
  selectedTaskIndex,
  currentTask,
  taskStatus,
  inputBuffer,
  loading,
  toast,
  lastRefreshAt,
  nodeCards,
  conversation,
  sessionEvents,
  nowActivePane,
  nodeScrollOffset,
  nodeStateScrollOffset,
  nodeCustomScrollOffset,
  toolScrollOffset,
  assistantScrollOffset,
  looperTakeover,
  looperEnabled,
  looperRunning,
  commandHelpVisible
} = storeToRefs(loopAI)

let pollTimer = null
const runtimeBottomSnapToken = ref(0)
const assistantBottomSnapToken = ref(0)

function local(text) {
  return appConfig.local(text)
}

function ensureNumber(value, fallback) {
  return Number.isFinite(value) ? value : fallback
}

function safeKey(event) {
  return String(event?.key || '')
}

function isNavigationAllowed() {
  return inputBuffer.value.trim().length === 0
}

async function quit() {
  props.onQuit?.()
}

async function submitCurrentInput() {
  try {
    const result = await loopAI.submitInput()
    if (result === 'quit') await quit()
  } catch (error) {
    loopAI.setToast(error?.message || String(error))
  }
}

async function moveSelection(delta) {
  try {
    if (page.value === 'tasks') {
      await loopAI.selectTaskByDelta(delta)
      return
    }
    if (page.value === 'now') {
      loopAI.scrollNowPaneBy(delta)
      return
    }
    loopAI.scrollBy(delta)
  } catch (error) {
    loopAI.setToast(error?.message || String(error))
  }
}

async function handleHotkey(event) {
  if (event?.defaultPrevented) return true
  const key = safeKey(event)
  const lowerKey = key.toLowerCase()

  if ((lowerKey === 'c' && event?.ctrlKey) || (lowerKey === 'q' && event?.ctrlKey)) {
    event.preventDefault?.()
    event.stopPropagation?.()
    await quit()
    return true
  }

  if (key === 'Escape') {
    event.preventDefault?.()
    event.stopPropagation?.()
    loopAI.clearInput()
    loopAI.setToast(local('Cancelled'))
    return true
  }

  if (key === 'Enter') {
    event.preventDefault?.()
    event.stopPropagation?.()
    if (page.value === 'tasks' && isNavigationAllowed()) {
      await loopAI.activateSelectedTask()
      return true
    }
    await submitCurrentInput()
    return true
  }

  if (!isNavigationAllowed()) return false

  if (key === 'ArrowDown' || lowerKey === 'k') {
    event.preventDefault?.()
    event.stopPropagation?.()
    await moveSelection(1)
    return true
  }
  if (key === 'ArrowUp' || lowerKey === 'j') {
    event.preventDefault?.()
    event.stopPropagation?.()
    await moveSelection(-1)
    return true
  }
  if (key === 'PageDown') {
    event.preventDefault?.()
    event.stopPropagation?.()
    await moveSelection(page.value === 'now' ? 5 : 10)
    return true
  }
  if (key === 'PageUp') {
    event.preventDefault?.()
    event.stopPropagation?.()
    await moveSelection(page.value === 'now' ? -5 : -10)
    return true
  }
  if (page.value === 'now' && (key === 'ArrowLeft' || lowerKey === 'h')) {
    event.preventDefault?.()
    event.stopPropagation?.()
    loopAI.scrollNodeBoardBy(-1)
    return true
  }
  if (page.value === 'now' && (key === 'ArrowRight' || lowerKey === 'l')) {
    event.preventDefault?.()
    event.stopPropagation?.()
    loopAI.scrollNodeBoardBy(1)
    return true
  }
  if (page.value === 'now' && key === 'Tab') {
    event.preventDefault?.()
    event.stopPropagation?.()
    loopAI.cycleNowPane()
    return true
  }

  return false
}

function onSurfaceKeydown(event) {
  handleHotkey(event)
}

function onInputKeydown(event) {
  handleHotkey(event)
}

function setInputValue(value) {
  inputBuffer.value = value
}

function handleToolPaneScrollTop(value) {
  toolScrollOffset.value = value
}

function handleAssistantPaneScrollTop(value) {
  assistantScrollOffset.value = value
}

function triggerNowBottomSnap() {
  toolScrollOffset.value = 1000000
  assistantScrollOffset.value = 1000000
  runtimeBottomSnapToken.value += 1
  assistantBottomSnapToken.value += 1
}

onMounted(async () => {
  try {
    await loopAI.bootstrap()
    pollTimer = setInterval(() => {
      loopAI.refreshCurrent(true).catch((error) => {
        loopAI.setToast(error?.message || String(error))
      })
    }, POLL_MS)
  } catch (error) {
    loopAI.setToast(error?.message || String(error))
  }
})

onBeforeUnmount(() => {
  if (pollTimer) clearInterval(pollTimer)
  loopAI.clearLooperTakeoverCountdown()
})

const cols = computed(() => ensureNumber(layout.clipRect?.w, 120))
const rows = computed(() => ensureNumber(layout.clipRect?.h, 36))
const headerHeight = 3
const footerHeight = computed(() => (commandHelpVisible.value ? 8 : 5))
const inputBoxHeight = 3
const bodyY = computed(() => headerHeight)
const bodyHeight = computed(() => Math.max(12, rows.value - headerHeight - footerHeight.value))
const inputY = computed(() => rows.value - footerHeight.value)
const apiBaseUrl = computed(() => loopAI.config?.base_url || taskStatus.value?.base_url || 'http://127.0.0.1:8855')
const commandHint = computed(() => '/h  /help  /home  /tasks  /now  /refresh  /clear  /stop  /stop_looper  /new <name>  /rename <name>  /delete  /quit')
const modeHint = computed(() => {
  if (page.value === 'tasks') return '↑/↓ select · Enter activate · /new /rename /delete'
  if (page.value === 'now') return '←/→ nodes · Tab pane · ↑/↓ scroll · PgUp/PgDn fast scroll'
  return '输入 /tasks 或 /now 进入任务视图'
})
const statusLine = computed(() => `${toast.value}   |   ${modeHint.value}`)

const commandPlaceholder = computed(() => {
  if (page.value === 'now') return '/h 查看命令帮助，或直接发送消息'
  if (page.value === 'tasks') return '/h 查看命令帮助，/now 打开当前任务'
  return '/h 查看命令帮助，/tasks 打开任务列表，/now 打开当前任务'
})

const commandHelpLines = computed(() => {
  if (!commandHelpVisible.value) return []
  if (page.value === 'now') {
    return [
      'Command Help · /now',
      '/refresh  同步当前任务状态与会话',
      '/clear  重置当前会话历史',
      '/stop  终止当前 starter codex 会话',
      '/stop_looper  中断 looper 接管或执行',
      '/tasks  回到任务列表   /home  回到首页'
    ]
  }
  return [
    'Command Help',
    '/tasks  打开任务列表',
    '/now  打开当前任务；如果未选择则打开第一个任务',
    '/new <name>  创建任务   /rename <name>  重命名任务',
    '/delete  删除当前任务   /refresh  刷新任务与状态',
    '/quit  退出'
  ]
})

watch(
  () => page.value,
  (value) => {
    if (value === 'now') triggerNowBottomSnap()
  }
)

watch(
  () => sessionEvents.value.length,
  (value, prevValue) => {
    if (page.value === 'now' && value > prevValue) triggerNowBottomSnap()
  }
)

watch(
  () => conversation.value.length,
  (value, prevValue) => {
    if (page.value === 'now' && value > prevValue) triggerNowBottomSnap()
  }
)
const homeHelpLines = computed(() => [
  'LoopAI terminal now runs on @simon_he/vue-tui.',
  'Use /h or /help to inspect available commands from the command area.',
  'Use /tasks to browse tasks, /now to open the selected task, /quit to leave.',
  tasks.value.length
    ? `Detected ${tasks.value.length} task(s). If no task is selected, /now will open the first one.`
    : local('No task yet. Press n to create one.')
])

const { taskItemHeight, taskPage, taskPageCount, visibleTasks } = useTaskPanel({
  tasks,
  selectedTaskIndex,
  bodyHeight
})

const {
  summaryHeight,
  nodesAreaY,
  nodesAreaHeight,
  chatAreaY,
  chatAreaHeight,
  nodeBoardLabel,
  visibleNodeCards,
  userMarkdown,
  runtimeMarkdown,
  transcriptMarkdown,
  toolScrollOffset: toolPaneScrollTop,
  assistantScrollOffset: assistantPaneScrollTop,
  nodeCardWidth,
  nodeCardGap
} = useNowPanel({
  bodyY,
  bodyHeight,
  bodyWidth: cols,
  local,
  currentTask,
  taskStatus,
  nodeCards,
  conversation,
  sessionEvents,
  nowActivePane,
  nodeScrollOffset,
  nodeStateScrollOffset,
  nodeCustomScrollOffset,
  toolScrollOffset,
  assistantScrollOffset
})

</script>

<template>
  <TView :x="0" :y="0" :w="cols" :h="rows" focusable autoFocus @keydownCapture="onSurfaceKeydown">
    <AppHeader
      :cols="cols"
      :page="page"
      :tasks-count="tasks.length"
      :loading="loading"
      :api-base-url="apiBaseUrl"
      :last-refresh-at="lastRefreshAt || '-'"
    />

    <HomePanel v-if="page === 'home'" :x="0" :y="bodyY" :w="cols" :h="bodyHeight" :help-lines="homeHelpLines" />

    <TaskList
      v-else-if="page === 'tasks'"
      :x="0"
      :y="bodyY"
      :w="cols"
      :h="bodyHeight"
      :title="`${local('Tasks')} (${tasks.length})`"
      :visible-tasks="visibleTasks"
      :selected-task-index="selectedTaskIndex"
      :task-item-height="taskItemHeight"
      :task-page="taskPage"
      :task-page-count="taskPageCount"
      :page-label="local('page')"
      :empty-text="local('No task yet. Press n to create one.')"
    />

    <template v-else>
      <NodeSummary
        :x="0"
        :y="bodyY"
        :w="cols"
        :h="summaryHeight"
        :current-task-name="currentTask?.name || currentTask?.task_id || '-'"
        :current-task-id="currentTask?.task_id || '-'"
        :status="taskStatus?.status || 'idle'"
        :node-count="nodeCards.length"
        :looper-enabled="looperEnabled"
        :looper-active="looperTakeover.active"
        :looper-pending="looperTakeover.pending"
        :looper-running="looperRunning"
        :looper-seconds="looperTakeover.seconds"
        :local="local"
      />
      <NodeBoard
        :x="0"
        :y="nodesAreaY"
        :w="cols"
        :h="nodesAreaHeight"
        :title="`${local('Nodes')} ${nodeBoardLabel}`"
        :visible-node-cards="visibleNodeCards"
        :now-active-pane="nowActivePane"
        :empty-text="local('No node data available for this task yet.')"
        :node-card-width="nodeCardWidth"
        :node-card-gap="nodeCardGap"
      />
      <TranscriptPane
        :x="0"
        :y="chatAreaY"
        :w="cols"
        :h="chatAreaHeight"
        :now-active-pane="nowActivePane"
        :user-markdown="userMarkdown"
        :runtime-markdown="runtimeMarkdown"
        :transcript-markdown="transcriptMarkdown"
        :tool-scroll-top="toolPaneScrollTop"
        :assistant-scroll-top="assistantPaneScrollTop"
        :runtime-bottom-snap-token="runtimeBottomSnapToken"
        :assistant-bottom-snap-token="assistantBottomSnapToken"
        @update:tool-scroll-top="handleToolPaneScrollTop"
        @update:assistant-scroll-top="handleAssistantPaneScrollTop"
      />
    </template>

    <AppFooter
      :x="0"
      :y="inputY"
      :w="cols"
      :input-box-height="inputBoxHeight"
      :model-value="inputBuffer"
      :placeholder="commandPlaceholder"
      :command-hint="commandHint"
      :status-line="statusLine"
      :help-lines="commandHelpLines"
      @update:model-value="setInputValue"
      @keydown="onInputKeydown"
    />
  </TView>
</template>
