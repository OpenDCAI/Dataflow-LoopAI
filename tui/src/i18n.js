export const i18n = {
  'LoopAI TUI': { en: 'LoopAI TUI', zh: 'LoopAI 终端' },
  'Tasks': { en: 'Tasks', zh: '任务' },
  'Nodes': { en: 'Nodes', zh: '节点' },
  'Chat': { en: 'Chat', zh: '会话' },
  'Node page': { en: 'page', zh: '页' },
  'Running nodes are pinned first, state/custom_info shown side by side': {
    en: 'running nodes are pinned first, state/custom_info shown side by side',
    zh: '运行中的节点会排在前面，state/custom_info 左右分栏显示'
  },
  'No task yet. Press n to create one.': { en: 'No task yet. Press n to create one.', zh: '还没有任务，按 n 创建一个。' },
  'No node data available for this task yet.': { en: 'No node data available for this task yet.', zh: '当前任务还没有节点数据。' },
  'Recent Conversation': { en: 'Recent Conversation', zh: '最近会话' },
  'Recent Runtime Output': { en: 'Recent Runtime Output', zh: '最近运行输出' },
  'Press v for full history and runtime events': { en: 'press v for full history and runtime events', zh: '按 v 查看完整历史和运行事件' },
  'Conversation Detail': { en: 'Conversation Detail', zh: '会话详情' },
  'Esc / v closes · j/k scroll': { en: 'Esc / v closes · j/k scroll', zh: 'Esc / v 关闭 · j/k 滚动' },
  'Create Task': { en: 'Create Task', zh: '创建任务' },
  'Rename Task': { en: 'Rename Task', zh: '重命名任务' },
  'Delete Task': { en: 'Delete Task', zh: '删除任务' },
  'Input a new task name and press Enter.': { en: 'Input a new task name and press Enter.', zh: '输入新任务名并按 Enter。' },
  'Edit the task name and press Enter.': { en: 'Edit the task name and press Enter.', zh: '编辑任务名并按 Enter。' },
  'Type yes and press Enter to delete the selected task.': {
    en: 'Type yes and press Enter to delete the selected task.',
    zh: '输入 yes 并按 Enter 删除当前选中任务。'
  },
  'Delete cancelled': { en: 'Delete cancelled', zh: '已取消删除' },
  'No task selected': { en: 'No task selected', zh: '未选择任务' },
  'Delete task failed': { en: 'Delete task failed', zh: '删除任务失败' },
  'Task deleted': { en: 'Task deleted', zh: '任务已删除' },
  'Name cannot be empty': { en: 'Name cannot be empty', zh: '名称不能为空' },
  'Create task failed': { en: 'Create task failed', zh: '创建任务失败' },
  'Task created': { en: 'Task created', zh: '任务已创建' },
  'Rename task failed': { en: 'Rename task failed', zh: '重命名任务失败' },
  'Task renamed': { en: 'Task renamed', zh: '任务已重命名' },
  'Cancelled': { en: 'Cancelled', zh: '已取消' },
  'Loading LoopAI TUI...': { en: 'Loading LoopAI TUI...', zh: '正在加载 LoopAI 终端...' },
  'Ready': { en: 'Ready', zh: '就绪' },
  'Failed to load tasks': { en: 'Failed to load tasks', zh: '加载任务失败' },
  'Refreshing': { en: 'Refreshing', zh: '正在刷新' },
  'Synced': { en: 'Synced', zh: '已同步' },
  'Session': { en: 'Session', zh: '会话' },
  'Status': { en: 'Status', zh: '状态' },
  'Conversation count': { en: 'Conversation count', zh: '会话数量' },
  'Event count': { en: 'Event count', zh: '事件数量' },
  'Conversation': { en: 'Conversation', zh: '会话内容' },
  'Runtime Events': { en: 'Runtime Events', zh: '运行事件' },
  'empty': { en: '(empty)', zh: '（空）' },
  'invalid payload': { en: 'invalid payload', zh: '无效负载' },
  'submitted': { en: 'submitted', zh: '已提交' },
  'unknown error': { en: 'unknown error', zh: '未知错误' },
  'model': { en: 'model', zh: '模型' },
  'base_url': { en: 'base_url', zh: '基础地址' },
  'assistant': { en: 'assistant', zh: '助手' },
  'user': { en: 'user', zh: '用户' },
  'tool': { en: 'tool', zh: '工具' },
  'todo': { en: 'todo', zh: '待办' },
  'command': { en: 'command', zh: '命令' },
  'file': { en: 'file', zh: '文件' },
  'init': { en: 'init', zh: '初始化' },
  'finishing': { en: 'finishing', zh: '收尾中' },
  'state': { en: 'state', zh: '状态' },
  'custom_info': { en: 'custom_info', zh: '实时信息' },
  'updated': { en: 'updated', zh: '更新时间' },
  'move': { en: 'move', zh: '移动' },
  'refresh': { en: 'refresh', zh: '刷新' },
  'new': { en: 'new', zh: '新建' },
  'rename': { en: 'rename', zh: '重命名' },
  'delete': { en: 'delete', zh: '删除' },
  'page': { en: 'page', zh: '翻页' },
  'detail': { en: 'detail', zh: '详情' },
  'sync': { en: 'sync', zh: '同步' },
  'quit': { en: 'quit', zh: '退出' },
  'Task': { en: 'Task', zh: '任务' },
  'Refresh': { en: 'Refresh', zh: '刷新' },
  'Unknown': { en: 'Unknown', zh: '未知' }
}

export function resolveLanguage(configData) {
  let language = 'en'
  try {
    language = configData?.states?.default?.language?.value || 'en'
  } catch (error) {}
  if (String(language).toLowerCase().startsWith('zh')) return 'zh'
  return 'en'
}

export function createLocal(languageGetter) {
  return (text) => {
    const language = typeof languageGetter === 'function' ? languageGetter() : languageGetter
    const entry = i18n[text]
    if (!entry) return text
    return entry[language] || entry.en || text
  }
}
