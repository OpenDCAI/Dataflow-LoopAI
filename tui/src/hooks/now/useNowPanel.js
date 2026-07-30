import { computed } from 'vue'
import { formatTime, formatValue, padRight, truncate, wrapText } from '../../lib/format.js'
import { normalizeConversationItem, summarizeEvent } from '../../lib/messages.js'

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value))
}

export function useNowPanel({
  bodyY,
  bodyHeight,
  bodyWidth,
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
}) {
  const NODE_CARD_WIDTH = 44
  const NODE_CARD_GAP = 2
  const NODE_CARD_HEIGHT_MIN = 15
  const NODE_CARD_HEIGHT_MAX = 20
  const summaryHeight = 7
  const nodesAreaY = computed(() => bodyY.value + summaryHeight)
  const nodesAreaHeight = computed(() => clamp(Math.floor(bodyHeight.value * 0.52), NODE_CARD_HEIGHT_MIN, NODE_CARD_HEIGHT_MAX))
  const chatAreaY = computed(() => nodesAreaY.value + nodesAreaHeight.value)
  const chatAreaHeight = computed(() => Math.max(8, bodyY.value + bodyHeight.value - chatAreaY.value))
  const visibleNodeCount = computed(() => Math.max(1, Math.floor((bodyWidth.value + NODE_CARD_GAP) / (NODE_CARD_WIDTH + NODE_CARD_GAP))))
  const maxNodeStart = computed(() => Math.max(0, nodeCards.value.length - visibleNodeCount.value))
  const nodeStart = computed(() => clamp(nodeScrollOffset.value || 0, 0, maxNodeStart.value))
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

  const visibleNodeCards = computed(() =>
    nodeCards.value.slice(nodeStart.value, nodeStart.value + visibleNodeCount.value).map((card, index) => ({
      ...card,
      borderStyle: index === 0 ? { fg: 'cyanBright' } : card.running ? { fg: 'greenBright' } : { fg: 'whiteBright' },
      focusLabel: index === 0 ? `focus=${nowActivePane.value}` : 'preview',
      focusStyle: { fg: index === 0 ? 'cyanBright' : 'white' },
      stateTitle: local('state'),
      customTitle: local('custom_info'),
      stateBorderStyle: index === 0 && nowActivePane.value === 'state' ? { fg: 'cyanBright' } : { fg: 'white' },
      customBorderStyle: index === 0 && nowActivePane.value === 'custom' ? { fg: 'magentaBright' } : { fg: 'white' },
      stateLines: stateLinesFor(card, index, Math.floor((NODE_CARD_WIDTH - 11) / 2), Math.max(1, nodesAreaHeight.value - 11)),
      customLines: customLinesFor(card, index, Math.floor((NODE_CARD_WIDTH - 11) / 2), Math.max(1, nodesAreaHeight.value - 11))
    }))
  )

  const transcriptEntries = computed(() => {
    const items = Array.isArray(conversation.value) ? conversation.value : []
    if (!items.length) return []
    return items.map((item) => normalizeConversationItem(item))
  })

  const eventEntries = computed(() => {
    const items = Array.isArray(sessionEvents.value) ? sessionEvents.value : []
    if (!items.length) return []
    return items.flatMap((item) => summarizeEvent(item.payload || {}, local))
  })

  const latestUser = computed(() => {
    for (let i = conversation.value.length - 1; i >= 0; i -= 1) {
      const item = normalizeConversationItem(conversation.value[i])
      if (item.role === 'user') return item.content
    }
    return '-'
  })

  const userMarkdown = computed(() => String(latestUser.value || '-'))

  const runtimeMarkdown = computed(() => {
    if (!eventEntries.value.length) return local('empty')
    return eventEntries.value.map((line) => `- ${String(line).replace(/^[-*]\s*/, '')}`).join('\n')
  })

  const transcriptMarkdown = computed(() => {
    if (!transcriptEntries.value.length) return local('empty')
    return transcriptEntries.value
      .map((item) => {
        const title = item.role === 'assistant' ? '### Assistant' : item.role === 'user' ? '### User' : `### ${String(item.role || 'message')}`
        return `${title}\n\n${item.content || ''}`
      })
      .join('\n\n')
  })

  return {
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
    toolScrollOffset,
    assistantScrollOffset,
    nodeCardWidth: NODE_CARD_WIDTH,
    nodeCardGap: NODE_CARD_GAP
  }
}
