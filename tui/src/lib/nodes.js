import { formatTime, formatValue } from './format.js'

export const NODE_SPECS = [
  { key: 'looper', label: 'Looper', prefixes: ['looper'], runtimeNodes: ['looper'] },
  { key: 'trainer', label: 'Trainer', prefixes: ['trainer'], runtimeNodes: ['trainer'] },
  { key: 'judger', label: 'Judger', prefixes: ['judger'], runtimeNodes: ['judger'] },
  { key: 'analyzer', label: 'Analyzer', prefixes: ['analyzer'], runtimeNodes: ['analyzer'] },
  { key: 'constructor', label: 'Constructor', prefixes: ['constructor'], runtimeNodes: ['constructor'] },
  { key: 'obtainer', label: 'Obtainer', prefixes: ['obtainer'], runtimeNodes: ['obtainer'] },
  { key: 'webcrawler', label: 'Webcrawler', prefixes: ['webcrawler'], runtimeNodes: ['webcrawler'] }
]

const pickRuntimeStatus = (runtimeList = [], spec) => {
  const matches = runtimeList.filter((item) => spec.runtimeNodes.includes(item.node_name))
  const running = matches.find((item) => item.status === 'running')
  return running || matches[0] || null
}

const buildStateEntries = (state = {}, spec) => {
  const agentState = state?.[spec.key]
  if (!agentState || typeof agentState !== 'object') return []
  return Object.entries(agentState).slice(0, 20).map(([key, value]) => ({
    key,
    value,
    text: `${key}: ${formatValue(value, 28)}`
  }))
}

const buildCustomGroups = (customInfo = {}, spec) => {
  return Object.entries(customInfo || {})
    .filter(([key]) => spec.prefixes.some((prefix) => key.startsWith(prefix)))
    .slice(0, 12)
    .map(([key, value]) => ({
      key,
      message: value?.message || '',
      progress: typeof value?.progress === 'number' ? value.progress : null,
      data: value?.data,
      updatedAt: value?.updated_at || value?.updatedAt || null,
      raw: value
    }))
}

const buildCustomLines = (groups = []) => {
  if (!groups.length) return ['-']
  return groups.map((group) => {
    const message = group.message ? `msg=${formatValue(group.message, 20)}` : null
    const progress = typeof group.progress === 'number' ? `progress=${Math.round(group.progress * 100)}%` : null
    const data = group.data && typeof group.data === 'object' ? `data=${formatValue(group.data, 20)}` : null
    return `${group.key}: ${[message, progress, data].filter(Boolean).join(' | ') || formatValue(group.raw, 20)}`
  })
}

export const buildNodeCards = (taskStatus = {}) => {
  const state = taskStatus?.state || {}
  const customInfo = taskStatus?.custom_info || {}
  const runtimeList = Array.isArray(taskStatus?.node_status) ? taskStatus.node_status : []
  const keys = new Set([
    ...NODE_SPECS.map((item) => item.key),
    ...Object.keys(state).filter((key) => NODE_SPECS.some((item) => item.key === key))
  ])

  const cards = [...keys].map((key) => {
    const spec = NODE_SPECS.find((item) => item.key === key) || {
      key,
      label: key,
      prefixes: [key],
      runtimeNodes: [key]
    }
    const runtime = pickRuntimeStatus(runtimeList, spec)
    const running = runtime?.status === 'running'
    const stateEntries = buildStateEntries(state, spec)
    const customGroups = buildCustomGroups(customInfo, spec)

    return {
      ...spec,
      running,
      runtimeStatus: runtime?.status || 'idle',
      runtimeUpdatedAt: runtime?.updated_at || runtime?.updatedAt || null,
      runtimeUpdatedLabel: formatTime(runtime?.updated_at || runtime?.updatedAt || null),
      stateEntries,
      customGroups,
      stateLines: stateEntries.length ? stateEntries.slice(0, 10).map((item) => item.text) : ['-'],
      customLines: buildCustomLines(customGroups)
    }
  })

  return cards.sort((a, b) => {
    if (a.running !== b.running) return a.running ? -1 : 1
    return a.label.localeCompare(b.label)
  })
}
