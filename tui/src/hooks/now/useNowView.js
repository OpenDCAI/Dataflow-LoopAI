import { computed, watch } from 'vue'
import { useWindowSize } from '@vue-tui/runtime'
import { storeToRefs } from 'pinia'
import { useAppConfig } from '../../stores/appConfig.js'
import { useLoopAI } from '../../stores/loopAI.js'
import { box, formatTime, formatValue, mergeColumns, padRight, wrapText } from '../../lib/format.js'
import { formatConversationLine, normalizeConversationItem, summarizeEvent } from '../../lib/messages.js'

const CARD_WIDTH = 46
const CARD_GAP = 2
const TRANSCRIPT_MIN_HEIGHT = 8
const TRANSCRIPT_MAX_HEIGHT = 18

function buildProgressBar(progress, width = 12) {
  if (typeof progress !== 'number') return null
  const clamped = Math.max(0, Math.min(1, progress))
  const filled = Math.round(clamped * width)
  return `[${'#'.repeat(filled)}${'.'.repeat(Math.max(0, width - filled))}] ${Math.round(clamped * 100)}%`
}

function buildStateColumn(appConfig, card, width) {
  const lines = [padRight(appConfig.local('state'), width), '-'.repeat(width)]
  if (!card.stateEntries.length) {
    lines.push('-')
    return lines
  }
  for (const entry of card.stateEntries) {
    lines.push(...wrapText(`${entry.key}: ${formatValue(entry.value, width * 2)}`, width))
    lines.push('')
  }
  return lines
}

function buildCustomBlock(group, width) {
  const body = []
  if (group.message) body.push(...wrapText(`msg: ${group.message}`, width - 2))
  if (typeof group.progress === 'number') body.push(buildProgressBar(group.progress, Math.max(6, width - 14)))
  if (group.data !== undefined) body.push(...wrapText(`data: ${formatValue(group.data, width * 2)}`, width - 2))
  if (group.updatedAt) body.push(...wrapText(`updated: ${formatTime(group.updatedAt)}`, width - 2))
  if (!body.length) body.push(formatValue(group.raw, width - 2))
  return box(group.key, body, width, Math.min(Math.max(body.length + 2, 4), 8))
}

function buildCustomColumn(appConfig, card, width) {
  const lines = [padRight(appConfig.local('custom_info'), width), '-'.repeat(width)]
  if (!card.customGroups.length) {
    lines.push('-')
    return lines
  }
  for (const group of card.customGroups) {
    lines.push(...buildCustomBlock(group, width))
    lines.push('')
  }
  return lines
}

function buildNodeCard(appConfig, card, width, height) {
  const innerWidth = Math.max(12, width - 2)
  const columnWidth = Math.max(8, Math.floor((innerWidth - 3) / 2))
  const stateLines = buildStateColumn(appConfig, card, columnWidth)
  const customLines = buildCustomColumn(appConfig, card, columnWidth)
  const body = [
    `status: ${card.running ? 'RUNNING' : card.runtimeStatus}`,
    `updated: ${card.runtimeUpdatedLabel || '-'}`,
    ''
  ]
  const mergedColumns = mergeColumns([stateLines, customLines], 3)
  body.push(...mergedColumns)
  return box(`${card.label} · ${card.running ? 'RUNNING' : card.runtimeStatus}`, body, width, height)
}

function colorForEntry(entry) {
  if (entry.kind === 'conversation') {
    if (entry.role === 'user') return 'cyan'
    if (entry.role === 'assistant') return 'green'
    if (entry.role === 'tool') return 'yellow'
    return 'white'
  }
  if (entry.kind === 'section') return 'magenta'
  if (entry.kind === 'event') return 'yellow'
  return 'white'
}

function buildTranscriptEntries(appConfig, conversation, sessionEvents, width) {
  const entries = []
  entries.push({ kind: 'section', text: appConfig.local('Conversation') })
  entries.push({ kind: 'divider', text: '------------', dim: true })
  if (!conversation.length) {
    entries.push({ kind: 'empty', text: appConfig.local('empty'), dim: true })
  } else {
    for (const item of conversation) {
      const normalized = normalizeConversationItem(item)
      const lines = formatConversationLine(normalized, width, appConfig.local)
      for (const line of lines) {
        entries.push({ kind: 'conversation', role: normalized.role, text: line })
      }
      entries.push({ kind: 'spacer', text: '' })
    }
  }

  entries.push({ kind: 'section', text: appConfig.local('Runtime Events') })
  entries.push({ kind: 'divider', text: '--------------', dim: true })
  if (!sessionEvents.length) {
    entries.push({ kind: 'empty', text: appConfig.local('empty'), dim: true })
  } else {
    for (const event of sessionEvents) {
      for (const line of summarizeEvent(event.payload || {}, appConfig.local)) {
        entries.push({ kind: 'event', text: line })
      }
      entries.push({ kind: 'spacer', text: '' })
    }
  }
  return entries
}

export function useNowView() {
  const appConfig = useAppConfig()
  const loopAI = useLoopAI()
  const { nodeCards, conversation, sessionEvents, scrollOffset, nodeScrollOffset } = storeToRefs(loopAI)
  const windowSize = useWindowSize()

  const columns = computed(() => windowSize.columns.value || 120)
  const rows = computed(() => windowSize.rows.value || 40)
  const contentWidth = computed(() => Math.max(40, columns.value - 8))
  const nodeBoardHeight = computed(() => Math.max(14, Math.min(20, rows.value - 20)))
  const cardsPerPage = computed(() => Math.max(1, Math.floor((contentWidth.value + CARD_GAP) / (CARD_WIDTH + CARD_GAP))))
  const nodePageCount = computed(() => Math.max(1, Math.ceil(nodeCards.value.length / cardsPerPage.value)))
  const currentNodePage = computed(() => {
    if (!nodeCards.value.length) return 1
    return Math.floor(nodeScrollOffset.value / cardsPerPage.value) + 1
  })

  const visibleNodeCards = computed(() => {
    const safeOffset = Math.max(0, Math.min(nodeScrollOffset.value, Math.max(0, nodeCards.value.length - 1)))
    return nodeCards.value.slice(safeOffset, safeOffset + cardsPerPage.value)
  })

  const nodeBoardLines = computed(() => {
    if (!visibleNodeCards.value.length) {
      return box(appConfig.local('Nodes'), [appConfig.local('No node data available for this task yet.')], contentWidth.value, 6)
    }
    const cards = visibleNodeCards.value.map((card) => buildNodeCard(appConfig, card, CARD_WIDTH, nodeBoardHeight.value))
    return mergeColumns(cards, CARD_GAP)
  })

  const transcriptHeight = computed(() => {
    const preferred = rows.value - nodeBoardHeight.value - 16
    return Math.max(TRANSCRIPT_MIN_HEIGHT, Math.min(TRANSCRIPT_MAX_HEIGHT, preferred))
  })
  const transcriptWidth = computed(() => Math.max(30, contentWidth.value - 4))
  const transcriptEntries = computed(() => buildTranscriptEntries(appConfig, conversation.value, sessionEvents.value, transcriptWidth.value))
  const maxScroll = computed(() => Math.max(0, transcriptEntries.value.length - transcriptHeight.value))
  const visibleTranscriptEntries = computed(() => transcriptEntries.value.slice(scrollOffset.value, scrollOffset.value + transcriptHeight.value))
  const nodeHint = computed(() => `nodes ${currentNodePage.value}/${nodePageCount.value}  ←/→ or h/l`)

  watch(
    transcriptEntries,
    () => {
      scrollOffset.value = maxScroll.value
    },
    { immediate: true }
  )

  const transcriptRenderItems = computed(() =>
    visibleTranscriptEntries.value.map((entry, index) => ({
      key: `transcript-${index}-${scrollOffset.value}`,
      text: entry.text,
      color: colorForEntry(entry),
      dim: Boolean(entry.dim)
    }))
  )

  return {
    nodeBoardLines,
    nodeHint,
    transcriptHeight,
    transcriptRenderItems,
    maxScroll
  }
}
