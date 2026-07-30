<script setup>
import { computed, onBeforeUnmount, onMounted, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { TBox, TInputBox, TText, TView, useLayout } from '@simon_he/vue-tui/vue'
import LogoPixel from './components/home/LogoPixel.vue'
import { useAppConfig } from './stores/appConfig.js'
import { useLoopAI } from './stores/loopAI.js'
import { formatTime, formatValue, padRight, truncate, wrapText } from './lib/format.js'
import { formatConversationLine, normalizeConversationItem, summarizeEvent } from './lib/messages.js'

const props = defineProps({
  onQuit: {
    type: Function,
    default: null
  }
})

const POLL_MS = 3000
const NODE_CARD_WIDTH = 44
const NODE_CARD_GAP = 2
const NODE_CARD_HEIGHT_MIN = 15
const NODE_CARD_HEIGHT_MAX = 20

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
  assistantScrollOffset
} = storeToRefs(loopAI)

let pollTimer = null

function local(text) {
  return appConfig.local(text)
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value))
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

  if (key === 'ArrowDown' || lowerKey === 'j') {
    event.preventDefault?.()
    event.stopPropagation?.()
    await moveSelection(1)
    return true
  }
  if (key === 'ArrowUp' || lowerKey === 'k') {
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
})

const cols = computed(() => ensureNumber(layout.clipRect?.w, 120))
const rows = computed(() => ensureNumber(layout.clipRect?.h, 36))

const headerHeight = 3
const footerHeight = 5
const inputBoxHeight = 3
const bodyY = computed(() => headerHeight)
const bodyHeight = computed(() => Math.max(12, rows.value - headerHeight - footerHeight))
const inputY = computed(() => rows.value - footerHeight)
const bodyWidth = computed(() => cols.value)

const apiBaseUrl = computed(() => loopAI.config?.base_url || taskStatus.value?.base_url || 'http://127.0.0.1:8855')

const commandHint = computed(() => '/home  /tasks  /now  /refresh  /new <name>  /rename <name>  /delete  /quit')
const modeHint = computed(() => {
  if (page.value === 'tasks') return '↑/↓ select · Enter activate · /new /rename /delete'
  if (page.value === 'now') return '←/→ nodes · Tab pane · ↑/↓ scroll · PgUp/PgDn fast scroll'
  return '输入 /tasks 或 /now 进入任务视图'
})

const homeHelpLines = computed(() => [
  'LoopAI terminal now runs on @simon_he/vue-tui.',
  'Use /tasks to browse tasks, /now to open the selected task, /quit to leave.',
  tasks.value.length
    ? `Detected ${tasks.value.length} task(s). If no task is selected, /now will open the first one.`
    : local('No task yet. Press n to create one.')
])

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
    task,
    actualIndex: taskStart.value + index
  }))
)

const summaryHeight = 4
const nodesAreaY = computed(() => bodyY.value + summaryHeight)
const nodesAreaHeight = computed(() => clamp(Math.floor(bodyHeight.value * 0.52), NODE_CARD_HEIGHT_MIN, NODE_CARD_HEIGHT_MAX))
const chatAreaY = computed(() => nodesAreaY.value + nodesAreaHeight.value)
const chatAreaHeight = computed(() => Math.max(8, bodyY.value + bodyHeight.value - chatAreaY.value))
const visibleNodeCount = computed(() => Math.max(1, Math.floor((bodyWidth.value + NODE_CARD_GAP) / (NODE_CARD_WIDTH + NODE_CARD_GAP))))
const maxNodeStart = computed(() => Math.max(0, nodeCards.value.length - visibleNodeCount.value))
const nodeStart = computed(() => clamp(nodeScrollOffset.value || 0, 0, maxNodeStart.value))
const visibleNodeCards = computed(() => nodeCards.value.slice(nodeStart.value, nodeStart.value + visibleNodeCount.value))
const focusedNode = computed(() => nodeCards.value[nodeStart.value] || null)
const nodeBoardLabel = computed(() => `${nodeCards.value.length ? nodeStart.value + 1 : 0}-${Math.min(nodeCards.value.length, nodeStart.value + visibleNodeCount.value)}/${nodeCards.value.length}`)

function progressBarLine(progress, width) {
  const safeWidth = Math.max(8, width)
  const ratio = clamp(Number.isFinite(progress) ? progress : 0, 0, 1)
  const percent = `${Math.round(ratio * 100)}%`
  const barWidth = Math.max(4, safeWidth - percent.length - 3)
  const filled = Math.round(barWidth * ratio)
  return `[${'='.repeat(filled)}${'-'.repeat(Math.max(0, barWidth - filled))}] ${percent}`
}

function stateLinesFor(card, index, width, height) {
  if (!card) return ['-']
  const lines = card.stateEntries?.length
    ? card.stateEntries.flatMap((entry) => wrapText(`${entry.key}: ${formatValue(entry.value, width * 3)}`, width).concat(['']))
    : ['-']
  const offset = index === 0 ? nodeStateScrollOffset.value : 0
  const maxOffset = Math.max(0, lines.length - height)
  const safeOffset = clamp(offset, 0, maxOffset)
  return lines.slice(safeOffset, safeOffset + height)
}

function customLinesFor(card, index, width, height) {
  if (!card) return ['-']
  const groups = Array.isArray(card.customGroups) ? card.customGroups : []
  const lines = groups.length
    ? groups.flatMap((group) => {
        const block = [
          `┌ ${truncate(group.key, Math.max(8, width - 4))}`,
          ...wrapText(`│ msg: ${group.message || '-'}`, Math.max(8, width)).map((line) => padRight(line, width)),
          `│ ${progressBarLine(typeof group.progress === 'number' ? group.progress : 0, Math.max(8, width - 2))}`,
          ...wrapText(`│ data: ${formatValue(group.data, width * 3)}`, Math.max(8, width)).map((line) => padRight(line, width)),
          `└ updated: ${formatTime(group.updatedAt)}`,
          ''
        ]
        return block
      })
    : ['-']
  const offset = index === 0 ? nodeCustomScrollOffset.value : 0
  const maxOffset = Math.max(0, lines.length - height)
  const safeOffset = clamp(offset, 0, maxOffset)
  return lines.slice(safeOffset, safeOffset + height)
}

const transcriptEntries = computed(() => {
  const items = Array.isArray(conversation.value) ? conversation.value : []
  if (!items.length) return [{ role: 'system', text: local('empty') }]
  return items.flatMap((item) => {
    const normalized = normalizeConversationItem(item)
    return formatConversationLine(normalized, Math.max(20, bodyWidth.value - 8), local).map((line) => ({
      role: normalized.role,
      text: line
    }))
  })
})

const eventEntries = computed(() => {
  const items = Array.isArray(sessionEvents.value) ? sessionEvents.value : []
  if (!items.length) return [{ tone: 'default', text: local('empty') }]
  return items.flatMap((item) => summarizeEvent(item.payload || {}, local).map((line) => ({
    tone: line.startsWith('[error]') ? 'error' : line.startsWith('[tool]') ? 'tool' : 'default',
    text: line
  })))
})

const transcriptBoxHeight = computed(() => clamp(chatAreaHeight.value - 2, 6, 14))
const eventBoxHeight = computed(() => clamp(chatAreaHeight.value - 2, 6, 12))
const latestUser = computed(() => {
  for (let i = conversation.value.length - 1; i >= 0; i -= 1) {
    const item = normalizeConversationItem(conversation.value[i])
    if (item.role === 'user') return item.content
  }
  return '-'
})

watch(
  [transcriptEntries, transcriptBoxHeight],
  () => {
    assistantScrollOffset.value = Math.max(0, transcriptEntries.value.length - Math.max(1, transcriptBoxHeight.value - 2))
  },
  { immediate: true }
)

watch(
  [eventEntries, eventBoxHeight],
  () => {
    toolScrollOffset.value = Math.max(0, eventEntries.value.length - Math.max(1, eventBoxHeight.value - 2))
  },
  { immediate: true }
)

const transcriptViewport = computed(() => {
  const innerHeight = Math.max(1, transcriptBoxHeight.value - 2)
  const safeOffset = clamp(assistantScrollOffset.value || 0, 0, Math.max(0, transcriptEntries.value.length - innerHeight))
  return transcriptEntries.value.slice(safeOffset, safeOffset + innerHeight)
})

const eventViewport = computed(() => {
  const innerHeight = Math.max(1, eventBoxHeight.value - 2)
  const safeOffset = clamp(toolScrollOffset.value || 0, 0, Math.max(0, eventEntries.value.length - innerHeight))
  return eventEntries.value.slice(safeOffset, safeOffset + innerHeight)
})

function lineStyleForRole(role) {
  if (role === 'user') return { fg: 'cyanBright' }
  if (role === 'assistant') return { fg: 'greenBright' }
  if (role === 'tool') return { fg: 'yellowBright' }
  return { fg: 'whiteBright' }
}

function lineStyleForEvent(tone) {
  if (tone === 'error') return { fg: 'redBright' }
  if (tone === 'tool') return { fg: 'yellowBright' }
  return { fg: 'whiteBright' }
}

const userPreviewLines = computed(() => wrapText(String(latestUser.value || '-'), Math.max(16, Math.floor(bodyWidth.value * 0.28) - 4)).slice(-3))
</script>

<template>
  <TView :x="0" :y="0" :w="cols" :h="rows" focusable autoFocus @keydown="onSurfaceKeydown">
    <TBox :x="0" :y="0" :w="cols" :h="headerHeight" border title="LoopAI TUI" :style="{ fg: 'cyanBright' }">
      <TText :x="1" :y="0" :w="Math.max(8, cols - 4)" :value="`/${page}   tasks=${tasks.length}   loading=${loading ? 'yes' : 'no'}`" />
      <TText :x="1" :y="1" :w="Math.max(8, cols - 4)" :value="`api=${apiBaseUrl}   updated=${lastRefreshAt || '-'}`" :style="{ fg: 'white' }" />
    </TBox>

    <template v-if="page === 'home'">
      <TBox :x="0" :y="bodyY" :w="cols" :h="bodyHeight" border title="Home" :style="{ fg: 'magentaBright' }">
        <LogoPixel :x="Math.max(2, Math.floor((cols - 92) / 2))" :y="2" />
        <TText
          v-for="(line, index) in homeHelpLines"
          :key="`home-help-${index}`"
          :x="Math.max(2, Math.floor((cols - 96) / 2))"
          :y="11 + index * 2"
          :w="Math.min(96, cols - 6)"
          :value="line"
          :style="index === 0 ? { fg: 'greenBright' } : { fg: 'whiteBright' }"
        />
      </TBox>
    </template>

    <template v-else-if="page === 'tasks'">
      <TBox :x="0" :y="bodyY" :w="cols" :h="bodyHeight" border :title="`${local('Tasks')} (${tasks.length})`" :style="{ fg: 'yellowBright' }">
        <TText :x="1" :y="0" :w="Math.max(12, cols - 6)" :value="'所有任务列表'" :style="{ fg: 'white' }" />
        <template v-if="visibleTasks.length">
          <template v-for="({ task, actualIndex }, index) in visibleTasks" :key="task.task_id || task.id || index">
            <TText
              :x="1"
              :y="1 + index * taskItemHeight"
              :w="Math.max(12, cols - 6)"
              :value="`${actualIndex === selectedTaskIndex ? '▶' : ' '} ${truncate(task.name || task.task_id || '-', Math.max(8, cols - 12))}`"
              :style="actualIndex === selectedTaskIndex ? { fg: 'greenBright', bold: true } : { fg: 'whiteBright' }"
            />
            <TText
              :x="3"
              :y="2 + index * taskItemHeight"
              :w="Math.max(12, cols - 8)"
              :value="`id=${task.task_id || '-'}   updated=${formatTime(task.updatedAt)}`"
              :style="{ fg: 'cyanBright' }"
            />
          </template>
        </template>
        <TText v-else :x="1" :y="1" :w="Math.max(12, cols - 6)" :value="local('No task yet. Press n to create one.')" :style="{ fg: 'redBright' }" />
        <TText :x="1" :y="Math.max(1, bodyHeight - 3)" :w="Math.max(12, cols - 6)" :value="`${local('page')} ${taskPage}/${taskPageCount}   Enter = activate task`" :style="{ fg: 'white' }" />
      </TBox>
    </template>

    <template v-else>
      <TBox :x="0" :y="bodyY" :w="cols" :h="summaryHeight" border title="Task Summary" :style="{ fg: 'cyanBright' }">
        <TText :x="1" :y="0" :w="Math.max(12, cols - 6)" :value="`任务: ${currentTask?.name || currentTask?.task_id || '-'}`" :style="{ fg: 'greenBright', bold: true }" />
        <TText :x="1" :y="1" :w="Math.max(12, cols - 6)" :value="`id: ${currentTask?.task_id || '-'}   状态: ${taskStatus?.status || 'idle'}   节点: ${nodeCards.length}`" />
      </TBox>

      <TBox
        :x="0"
        :y="nodesAreaY"
        :w="cols"
        :h="nodesAreaHeight"
        border
        :title="`${local('Nodes')} ${nodeBoardLabel}`"
        :style="{ fg: 'yellowBright' }"
      >
        <template v-if="visibleNodeCards.length">
          <template v-for="(card, index) in visibleNodeCards" :key="`${card.key}-${index}`">
            <TBox
              :x="index * (NODE_CARD_WIDTH + NODE_CARD_GAP)"
              :y="0"
              :w="NODE_CARD_WIDTH"
              :h="Math.max(8, nodesAreaHeight - 2)"
              border
              :title="`${card.label} · ${card.runtimeStatus}`"
              :style="index === 0 ? { fg: 'cyanBright' } : card.running ? { fg: 'greenBright' } : { fg: 'whiteBright' }"
            >
              <TText :x="1" :y="0" :w="NODE_CARD_WIDTH - 4" :value="`updated: ${card.runtimeUpdatedLabel || '-'}`" :style="{ fg: 'white' }" />
              <TText :x="1" :y="1" :w="NODE_CARD_WIDTH - 4" :value="index === 0 ? `focus=${nowActivePane}` : 'preview'" :style="{ fg: index === 0 ? 'cyanBright' : 'white' }" />

              <TBox
                :x="1"
                :y="3"
                :w="Math.floor((NODE_CARD_WIDTH - 5) / 2)"
                :h="Math.max(6, nodesAreaHeight - 7)"
                border
                :title="local('state')"
                :style="index === 0 && nowActivePane === 'state' ? { fg: 'cyanBright' } : { fg: 'white' }"
              >
                <TText
                  v-for="(line, lineIndex) in stateLinesFor(card, index, Math.floor((NODE_CARD_WIDTH - 11) / 2), Math.max(1, nodesAreaHeight - 11))"
                  :key="`${card.key}-state-${lineIndex}`"
                  :x="0"
                  :y="lineIndex"
                  :w="Math.floor((NODE_CARD_WIDTH - 11) / 2)"
                  :value="line"
                  :style="{ fg: 'whiteBright' }"
                />
              </TBox>

              <TBox
                :x="Math.floor((NODE_CARD_WIDTH - 5) / 2) + 2"
                :y="3"
                :w="Math.floor((NODE_CARD_WIDTH - 5) / 2)"
                :h="Math.max(6, nodesAreaHeight - 7)"
                border
                :title="local('custom_info')"
                :style="index === 0 && nowActivePane === 'custom' ? { fg: 'magentaBright' } : { fg: 'white' }"
              >
                <TText
                  v-for="(line, lineIndex) in customLinesFor(card, index, Math.floor((NODE_CARD_WIDTH - 11) / 2), Math.max(1, nodesAreaHeight - 11))"
                  :key="`${card.key}-custom-${lineIndex}`"
                  :x="0"
                  :y="lineIndex"
                  :w="Math.floor((NODE_CARD_WIDTH - 11) / 2)"
                  :value="line"
                  :style="line.startsWith('┌') || line.startsWith('└') ? { fg: 'yellowBright' } : { fg: 'whiteBright' }"
                />
              </TBox>
            </TBox>
          </template>
        </template>
        <TText v-else :x="1" :y="1" :w="Math.max(12, cols - 6)" :value="local('No node data available for this task yet.')" :style="{ fg: 'redBright' }" />
      </TBox>

      <TBox :x="0" :y="chatAreaY" :w="Math.floor(cols * 0.3)" :h="chatAreaHeight" border title="User" :style="{ fg: 'cyanBright' }">
        <TText
          v-for="(line, index) in userPreviewLines"
          :key="`user-preview-${index}`"
          :x="1"
          :y="1 + index"
          :w="Math.max(8, Math.floor(cols * 0.3) - 4)"
          :value="line"
          :style="{ fg: 'cyanBright' }"
        />
      </TBox>

      <TBox
        :x="Math.floor(cols * 0.3)"
        :y="chatAreaY"
        :w="Math.floor(cols * 0.3)"
        :h="chatAreaHeight"
        border
        title="Runtime"
        :style="{ fg: nowActivePane === 'tool' ? 'yellowBright' : 'whiteBright' }"
      >
        <TText
          v-for="(line, index) in eventViewport"
          :key="`event-line-${index}`"
          :x="1"
          :y="1 + index"
          :w="Math.max(8, Math.floor(cols * 0.3) - 4)"
          :value="line.text"
          :style="lineStyleForEvent(line.tone)"
        />
      </TBox>

      <TBox
        :x="Math.floor(cols * 0.6)"
        :y="chatAreaY"
        :w="cols - Math.floor(cols * 0.6)"
        :h="chatAreaHeight"
        border
        title="Transcript"
        :style="{ fg: nowActivePane === 'assistant' ? 'greenBright' : 'magentaBright' }"
      >
        <TText
          v-for="(line, index) in transcriptViewport"
          :key="`transcript-line-${index}`"
          :x="1"
          :y="1 + index"
          :w="Math.max(8, cols - Math.floor(cols * 0.6) - 4)"
          :value="line.text"
          :style="lineStyleForRole(line.role)"
        />
      </TBox>
    </template>

    <TInputBox
      :x="0"
      :y="inputY"
      :w="cols"
      :h="inputBoxHeight"
      title="Command"
      :model-value="inputBuffer"
      placeholder="/tasks, /now, /new demo, 或直接发送消息"
      :style="{ fg: 'whiteBright' }"
      auto-focus
      @update:model-value="setInputValue"
      @keydown="onInputKeydown"
    />
    <TText :x="2" :y="inputY + 2" :w="Math.max(12, cols - 6)" :value="commandHint" :style="{ fg: 'white' }" />
    <TText :x="2" :y="inputY + 3" :w="Math.max(12, cols - 6)" :value="`${toast}   |   ${modeHint}`" :style="{ fg: 'greenBright' }" />
  </TView>
</template>
