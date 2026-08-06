import { computed, watch } from 'vue'
import { useWindowSize } from '@vue-tui/runtime'
import { storeToRefs } from 'pinia'
import { useAppConfig } from '../../stores/appConfig.js'
import { useLoopAI } from '../../stores/loopAI.js'
import { formatTime, formatValue, wrapText } from '../../lib/format.js'
import { normalizeConversationItem, summarizeEvent } from '../../lib/messages.js'

function clampOffset(offset, max) {
  return Math.max(0, Math.min(offset, Math.max(0, max)))
}

function slicePane(lines, offset, height) {
  const safeHeight = Math.max(1, height)
  const maxOffset = Math.max(0, lines.length - safeHeight)
  const safeOffset = clampOffset(offset, maxOffset)
  return {
    lines: lines.slice(safeOffset, safeOffset + safeHeight),
    maxOffset,
    safeOffset,
    summary: `${Math.min(lines.length, safeOffset + 1)}-${Math.min(lines.length, safeOffset + safeHeight)}/${lines.length || 0}`
  }
}

function buildStateLines(card, width) {
  if (!card?.stateEntries?.length) return ['-']
  return card.stateEntries.flatMap((entry) => {
    const lines = wrapText(`${entry.key}: ${formatValue(entry.value, width * 3)}`, width)
    return [...lines, '']
  })
}

function buildCustomLines(card, width) {
  if (!card?.customGroups?.length) return ['-']
  return card.customGroups.flatMap((group) => {
    const lines = [group.key]
    if (group.message) lines.push(...wrapText(`msg: ${group.message}`, width))
    if (typeof group.progress === 'number') lines.push(`progress: ${Math.round(group.progress * 100)}%`)
    if (group.data !== undefined) lines.push(...wrapText(`data: ${formatValue(group.data, width * 3)}`, width))
    if (group.updatedAt) lines.push(`updated: ${formatTime(group.updatedAt)}`)
    return [...lines, '-'.repeat(Math.max(6, width - 2))]
  })
}

function latestConversationByRole(conversation, role) {
  for (let i = conversation.length - 1; i >= 0; i -= 1) {
    const item = normalizeConversationItem(conversation[i])
    if (item.role === role) return item
  }
  return null
}

function buildToolLines(events, local, width) {
  if (!events.length) return [local('empty')]
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const lines = summarizeEvent(events[i].payload || {}, local)
    if (lines?.length) {
      return lines.flatMap((line) => wrapText(line, width).concat(['']))
    }
  }
  return [local('empty')]
}

function buildAssistantLines(conversation, width) {
  const assistant = latestConversationByRole(conversation, 'assistant')
  if (!assistant?.content) return ['Thinking...']
  return wrapText(assistant.content, width)
}

function buildUserLines(conversation, width) {
  const user = latestConversationByRole(conversation, 'user')
  if (!user?.content) return ['-']
  return wrapText(user.content, width)
}

export function useNowView() {
  const appConfig = useAppConfig()
  const loopAI = useLoopAI()
  const {
    nodeCards,
    conversation,
    sessionEvents,
    nodeScrollOffset,
    nowActivePane,
    nodeStateScrollOffset,
    nodeCustomScrollOffset,
    toolScrollOffset,
    assistantScrollOffset
  } = storeToRefs(loopAI)
  const windowSize = useWindowSize()

  const columns = computed(() => windowSize.columns.value || 120)
  const rows = computed(() => windowSize.rows.value || 40)
  const contentWidth = computed(() => Math.max(50, columns.value - 8))
  const nodePaneHeight = computed(() => Math.max(8, Math.min(12, Math.floor(rows.value * 0.28))))
  const chatPaneHeight = computed(() => Math.max(8, Math.min(14, Math.floor(rows.value * 0.34))))
  const paneInnerWidth = computed(() => Math.max(18, Math.floor((contentWidth.value - 8) / 2)))
  const assistantWidth = computed(() => Math.max(24, contentWidth.value - 6))

  const focusedNodeIndex = computed(() => clampOffset(nodeScrollOffset.value, nodeCards.value.length - 1))
  const focusedNode = computed(() => nodeCards.value[focusedNodeIndex.value] || null)
  const nodeHint = computed(() => `node ${nodeCards.value.length ? focusedNodeIndex.value + 1 : 0}/${nodeCards.value.length}  pane=${nowActivePane.value}  Tab switch · ←/→ node · j/k scroll`)

  const stateLines = computed(() => buildStateLines(focusedNode.value, paneInnerWidth.value))
  const customLines = computed(() => buildCustomLines(focusedNode.value, paneInnerWidth.value))
  const userLines = computed(() => buildUserLines(conversation.value, assistantWidth.value))
  const toolLines = computed(() => buildToolLines(sessionEvents.value, appConfig.local, assistantWidth.value))
  const assistantLines = computed(() => buildAssistantLines(conversation.value, assistantWidth.value))

  const stateViewport = computed(() => slicePane(stateLines.value, nodeStateScrollOffset.value, nodePaneHeight.value))
  const customViewport = computed(() => slicePane(customLines.value, nodeCustomScrollOffset.value, nodePaneHeight.value))
  const toolViewport = computed(() => slicePane(toolLines.value, toolScrollOffset.value, 4))
  const assistantViewportHeight = computed(() => Math.max(4, chatPaneHeight.value - 7))
  const assistantViewport = computed(() => slicePane(assistantLines.value, assistantScrollOffset.value, assistantViewportHeight.value))

  watch(
    [assistantLines, toolLines],
    () => {
      assistantScrollOffset.value = Math.max(0, assistantLines.value.length - assistantViewportHeight.value)
      toolScrollOffset.value = Math.max(0, toolLines.value.length - 4)
    },
    { immediate: true }
  )

  const paneBorderColor = computed(() => ({
    state: nowActivePane.value === 'state' ? 'cyan' : 'white',
    custom: nowActivePane.value === 'custom' ? 'cyan' : 'white',
    tool: nowActivePane.value === 'tool' ? 'yellow' : 'white',
    assistant: nowActivePane.value === 'assistant' ? 'green' : 'white'
  }))

  return {
    focusedNode,
    nodeHint,
    nodePaneHeight,
    chatPaneHeight,
    paneBorderColor,
    stateViewport,
    customViewport,
    userLines,
    toolViewport,
    assistantViewport,
    assistantWidth
  }
}
