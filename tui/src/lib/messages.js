import { formatValue, wrapText } from './format.js'

const identity = (text) => text

export const normalizeConversationItem = (item = {}) => {
  const role = item.role || 'message'
  const content = typeof item.content === 'string' ? item.content : formatValue(item.content, 160)
  return {
    role,
    content,
    id: item.id || null,
    state: item.state || null
  }
}

export const recentConversation = (conversation = [], count = 2) =>
  conversation.slice(Math.max(0, conversation.length - count)).map(normalizeConversationItem)

export const formatConversationLine = (item, width, local = identity) => {
  const role = item?.role || 'message'
  const roleText = role === 'assistant' ? local('assistant') : role === 'user' ? local('user') : role === 'tool' ? local('tool') : role.toUpperCase()
  const prefix = `[${String(roleText).toUpperCase()}] `
  const bodyWidth = Math.max(10, width - prefix.length)
  const lines = wrapText(item?.content || '', bodyWidth)
  return lines.map((line, index) => (index === 0 ? `${prefix}${line}` : `${' '.repeat(prefix.length)}${line}`))
}

const formatTodoList = (items = []) =>
  items.map((item) => `- [${item?.completed ? 'x' : ' '}] ${item?.text || ''}`)

export const summarizeEvent = (payload = {}, local = identity) => {
  if (!payload || typeof payload !== 'object') return [`[event] ${local('invalid payload')}`]
  const type = payload.type || 'unknown'
  if (type === 'submitted') return [`[${local('Status').toLowerCase()}] ${payload.message || local('submitted')}`]
  if (type === 'starter.codex.init') {
    return [
      `[${local('init')}] ${local('model')}: ${payload.model || '-'}`,
      `[${local('init')}] ${local('base_url')}: ${payload.base_url || '-'}`
    ]
  }
  if (type === 'runner.started') return [`[${local('user')}] ${payload.prompt || ''}`]
  if (type === 'finishing') return [`[${local('finishing')}] ${payload.message || ''}`]
  if (type === 'completed') return [`[${local('assistant')}] ${payload?.result?.finalResponse || ''}`]
  if (type !== 'event') {
    return [`[${type}] ${payload.message || ''}`]
  }

  const event = payload.event || {}
  if (event.type === 'error') {
    return [`[error] ${event.message || local('unknown error')}`]
  }

  const item = event.item || {}
  if (!item.type) {
    return [`[event:${event.type || local('Unknown').toLowerCase()}]`]
  }
  if (item.type === 'agent_message') return [`[${local('assistant')}] ${item.text || ''}`]
  if (item.type === 'mcp_tool_call') {
    return [
      `[${local('tool')}] ${item.server || '-'} / ${item.tool || '-'}`,
      `[args] ${formatValue(item.arguments, 120)}`
    ]
  }
  if (item.type === 'todo_list') {
    return [`[${local('todo')}]`].concat(formatTodoList(item.items || []))
  }
  if (item.type === 'command_execution') {
    return [`[${local('command')}:${event.type}] ${item.command || ''}`]
  }
  if (item.type === 'file_change') {
    const firstChange = item.changes?.[0]?.path || '-'
    return [`[${local('file')}:${event.type}] ${firstChange}`]
  }
  return [`[event:${item.type}] ${formatValue(item, 120)}`]
}

export const formatDetailTranscript = (session = {}, width = 100, local = identity) => {
  const lines = []
  const conversation = Array.isArray(session.conversation) ? session.conversation : []
  const events = Array.isArray(session.events) ? session.events : []

  lines.push(`${local('Session')}: ${session.session_id || '-'}`)
  lines.push(`${local('Status')}: ${session.status || local('Unknown').toLowerCase()}`)
  lines.push(`${local('Conversation count')}: ${conversation.length}`)
  lines.push(`${local('Event count')}: ${events.length}`)
  lines.push('')
  lines.push(local('Conversation'))
  lines.push('------------')
  if (!conversation.length) {
    lines.push(local('empty'))
  } else {
    conversation.forEach((item, index) => {
      lines.push(`#${index + 1}`)
      lines.push(...formatConversationLine(normalizeConversationItem(item), width - 2, local))
      lines.push('')
    })
  }

  lines.push(local('Runtime Events'))
  lines.push('--------------')
  if (!events.length) {
    lines.push(local('empty'))
  } else {
    events.forEach((event, index) => {
      lines.push(`#${index + 1}`)
      const eventLines = summarizeEvent(event.payload || {}, local)
      for (const line of eventLines) {
        lines.push(...wrapText(line, width - 2))
      }
      lines.push('')
    })
  }
  return lines
}
