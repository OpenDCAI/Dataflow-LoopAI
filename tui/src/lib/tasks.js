function firstNonEmpty(...values) {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return ''
}

export function normalizeTask(task) {
  if (!task || typeof task !== 'object') return null

  const normalized = {
    ...task
  }

  normalized.id = task.id ?? task.taskId ?? task.task_id ?? null
  normalized.task_id = firstNonEmpty(task.task_id, task.taskId, task.id != null ? String(task.id) : '')
  normalized.name = firstNonEmpty(
    task.name,
    task.task_name,
    task.title,
    task.label,
    task.display_name,
    task.task_id,
    task.taskId
  )
  normalized.updatedAt = task.updatedAt ?? task.updated_at ?? task.modifiedAt ?? task.createdAt ?? task.created_at ?? null
  normalized.createdAt = task.createdAt ?? task.created_at ?? normalized.updatedAt

  return normalized
}

export function normalizeTaskList(payload) {
  const source = Array.isArray(payload)
    ? payload
    : Array.isArray(payload?.items)
      ? payload.items
      : Array.isArray(payload?.records)
        ? payload.records
        : []

  return source.map(normalizeTask).filter(Boolean)
}
