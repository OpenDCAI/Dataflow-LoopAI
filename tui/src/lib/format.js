const ANSI_PATTERN = /\u001b\[[0-9;]*m/g

export const stripAnsi = (value = '') => String(value).replace(ANSI_PATTERN, '')

export const visibleLength = (value = '') => stripAnsi(value).length

export const truncate = (value, width) => {
  const text = value == null ? '' : String(value)
  if (width <= 0) return ''
  if (visibleLength(text) <= width) return text
  if (width === 1) return '.'
  if (width === 2) return '..'
  return `${stripAnsi(text).slice(0, width - 3)}...`
}

export const padRight = (value, width) => {
  const text = value == null ? '' : String(value)
  const len = visibleLength(text)
  if (len >= width) return truncate(text, width)
  return `${text}${' '.repeat(width - len)}`
}

export const wrapText = (value, width) => {
  const source = value == null ? '' : String(value)
  if (width <= 1) return [truncate(source, Math.max(width, 1))]
  const paragraphs = source.replace(/\r/g, '').split('\n')
  const lines = []
  for (const paragraph of paragraphs) {
    if (!paragraph) {
      lines.push('')
      continue
    }
    let remaining = paragraph
    while (remaining.length > width) {
      let cursor = remaining.lastIndexOf(' ', width)
      if (cursor <= 0) cursor = width
      lines.push(remaining.slice(0, cursor).trimEnd())
      remaining = remaining.slice(cursor).trimStart()
    }
    lines.push(remaining)
  }
  return lines.length ? lines : ['']
}

export const formatValue = (value, maxWidth = 48) => {
  if (value == null) return 'null'
  if (typeof value === 'string') return truncate(value.replace(/\s+/g, ' ').trim(), maxWidth)
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try {
    return truncate(JSON.stringify(value), maxWidth)
  } catch (error) {
    return truncate(String(value), maxWidth)
  }
}

export const formatTime = (value) => {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleString('zh-CN', {
    hour12: false
  })
}

export const box = (title, bodyLines, width, height) => {
  const innerWidth = Math.max(1, width - 2)
  const lines = []
  const titleText = title ? ` ${truncate(title, innerWidth - 2)} ` : ''
  const top = `+${padRight(titleText, innerWidth).replace(/ /g, '-') }+`
  lines.push(top)
  const usableHeight = Math.max(0, height - 2)
  for (let i = 0; i < usableHeight; i += 1) {
    const line = bodyLines[i] || ''
    lines.push(`|${padRight(line, innerWidth)}|`)
  }
  lines.push(`+${'-'.repeat(innerWidth)}+`)
  return lines
}

export const mergeColumns = (columns, gap = 1) => {
  const maxRows = Math.max(0, ...columns.map((item) => item.length))
  const rows = []
  for (let row = 0; row < maxRows; row += 1) {
    rows.push(columns.map((item) => item[row] || ' '.repeat(visibleLength(item[0] || ''))).join(' '.repeat(gap)))
  }
  return rows
}
