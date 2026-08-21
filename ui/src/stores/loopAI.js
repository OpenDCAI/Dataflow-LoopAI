import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { getCurrentInstance } from 'vue'

import { getBaseURL } from '@/axios/config'

export const useLoopAI = defineStore('useLoopAI', () => {
    const instance = getCurrentInstance()
    const proxy = instance.proxy

    const currentTask = ref(null)

    const configId = ref(null)
    const config = ref({
        system: {},
        states: {}
    })
    const getConfigs = async () => {
        let resp = await proxy.$api.config.getConfig().then((res) => {
            if (res.data) {
                configId.value = res.data.id
                let { system, states } = res.data
                config.value.system = system
                config.value.states = states
            }
            return res
        })
        return resp
    }

    const resources = ref([])
    const getResources = async () => {
        await proxy.$api.resource.getResource().then((res) => {
            if (res.code === 200) {
                let _resources = res.data || []
                _resources.forEach((item) => {
                    item.showPreview = false
                    item.expanded = false
                })
                resources.value = _resources
            } else {
                proxy.$barWarning(res.message, {
                    status: 'warning'
                })
            }
        })
    }

    const LAST_TASK_STORAGE_KEY = 'loopai-last-task'

    const readLastTaskId = () => {
        try {
            return localStorage.getItem(LAST_TASK_STORAGE_KEY)
        } catch (error) {
            return null
        }
    }
    const writeLastTaskId = (taskId) => {
        try {
            if (taskId) localStorage.setItem(LAST_TASK_STORAGE_KEY, taskId)
            else localStorage.removeItem(LAST_TASK_STORAGE_KEY)
        } catch (error) {
            /* private mode — the workspace just opens on the newest task */
        }
    }

    const tasks = ref([])
    const getTasks = async () => {
        await proxy.$api.task.getTasks().then((res) => {
            if (res.code === 200) {
                let _tasks = res.data || []
                _tasks.forEach((item) => {
                    item.show = true
                })
                _tasks.sort((a, b) => new Date(b.updatedAt || 0) - new Date(a.updatedAt || 0))
                tasks.value = _tasks
            } else {
                proxy.$barWarning(res.message, {
                    status: 'warning'
                })
            }
        })
    }

    /**
     * Opening the workspace should never ask a question it can answer itself:
     * resume the task you were last on, else the most recently touched one.
     */
    const resumeTask = async () => {
        if (currentTask.value?.task_id) return currentTask.value
        await getTasks()
        if (!tasks.value.length) return null
        const lastId = readLastTaskId()
        const resumed = tasks.value.find((item) => item.task_id === lastId) || tasks.value[0]
        await setCurrentTask(resumed)
        return resumed
    }

    const createTask = async (name) => {
        const taskName = (name || '').trim() || `task-${new Date().toISOString().slice(0, 16).replace(/[-:T]/g, '')}`
        const res = await proxy.$api.task.createTask({
            name: taskName,
            config: JSON.stringify(config.value),
            state: ''
        })
        if (res.code !== 200) {
            proxy.$barWarning(res.message || 'Failed to create the task.', { status: 'warning' })
            return null
        }
        await getTasks()
        const created = tasks.value.find((item) => item.task_id === res.data?.task_id) || res.data
        await setCurrentTask(created)
        return created
    }

    const renameTask = async (task, name) => {
        if (!task?.id || !name?.trim()) return false
        const res = await proxy.$api.task.updateTask({ id: task.id, name: name.trim() })
        if (res.code !== 200) {
            proxy.$barWarning(res.message || 'Failed to rename the task.', { status: 'warning' })
            return false
        }
        if (currentTask.value?.task_id === task.task_id) currentTask.value.name = name.trim()
        await getTasks()
        return true
    }

    const deleteTask = async (task) => {
        if (!task?.id) return false
        const res = await proxy.$api.task.delTask(task.id)
        if (res.code !== 200) {
            proxy.$barWarning(res.message || 'Failed to delete the task.', { status: 'warning' })
            return false
        }
        const wasCurrent = currentTask.value?.task_id === task.task_id
        await getTasks()
        if (wasCurrent) await setCurrentTask(tasks.value[0] || null)
        return true
    }

    const emptyTaskStatus = () => ({
        started: false,
        running: false,
        waiting_llm: true,
        event_streaming: 'not_ready',
        current: null,
        node_status: null,
        interrupt_value: 'input the human query',
        state: null,
        custom_info: null,
        update_custom_info: null
    })
    const taskStatus = ref(emptyTaskStatus())
    const getStatus = async (task_id = currentTask.value?.task_id) => {
        if (!task_id) return
        const requestedTaskId = task_id
        await proxy.$api.starter
            .getAgentStatus(task_id)
            .then((res) => {
                if (currentTask.value?.task_id !== requestedTaskId) return
                if (res.code === 200) {
                    taskStatus.value.started = true
                    for (let key in taskStatus.value) {
                        if (res.data[key] !== undefined) taskStatus.value[key] = res.data[key]
                    }
                    getMessages()
                } else {
                    taskStatus.value.started = false
                    taskStatus.value.running = false
                    taskStatus.value.waiting_llm = false
                }
            })
            .catch(() => {
                taskStatus.value.running = false
                taskStatus.value.waiting_llm = false
                proxy.$barWarning('server connection error', {
                    status: 'error'
                })
            })
    }
    const taskMessages = ref([])
    const msgStreamModel = ref({
        msgs: [],
        loading: false,
        status: 'stale'
    })
    const looperTakeover = ref({
        timer: null,
        seconds: 15,
        duration: 15,
        active: false
    })
    const clearLooperTakeoverCountdown = ({ resetSeconds = true, keepActive = false } = {}) => {
        if (looperTakeover.value.timer) {
            clearInterval(looperTakeover.value.timer)
            looperTakeover.value.timer = null
        }
        looperTakeover.value.active = keepActive ? looperTakeover.value.active : false
        if (resetSeconds) {
            looperTakeover.value.seconds = looperTakeover.value.duration
        }
    }
    const setLooperTakeoverCountdown = ({
        seconds = looperTakeover.value.seconds,
        duration = looperTakeover.value.duration,
        active = looperTakeover.value.active
    } = {}) => {
        looperTakeover.value.duration = duration
        looperTakeover.value.seconds = seconds
        looperTakeover.value.active = active
    }
    
    const msgEventSource = ref(null)
    const msgEventKeys = ref(new Set())


    const setCurrentTask = async (task) => {
        currentTask.value = task || null
        writeLastTaskId(task?.task_id || null)
        taskStatus.value = emptyTaskStatus()
        taskMessages.value = []
        msgStreamModel.value.msgs = []
        msgStreamModel.value.loading = false
        msgStreamModel.value.status = 'stale'
        closeMsgStream()
        clearLooperTakeoverCountdown()
        await getMessages()
    }
    const getMessages = async () => {
        if (!currentTask.value?.task_id) return;
        const normalizeConversationMessage = (item) => {

            return {
                type: item?.role,
                data: {
                    id: item?.id,
                    role: item?.role,
                    state: item?.state,
                    content: item?.content || ''
                }
            }
        }
        await proxy.$api.starter.starterCodexSession(currentTask.value?.task_id)
            .then((res) => {
                if (res.code === 200) {
                    taskMessages.value = (res.data?.conversation || []).map((item) =>
                        normalizeConversationMessage(item)
                    )
                    const streamStatus = res?.data?.status || 'stale'
                    const shouldStream = ['submitted', 'running', 'finishing'].includes(streamStatus)
                    msgStreamModel.value.status = streamStatus
                    msgStreamModel.value.loading = shouldStream
                    if (shouldStream && !msgEventSource.value) {
                        getMsgStream()
                    }
                }
            })
            .catch(() => {
                msgStreamModel.value.loading = false
            })
    }

    const resetStarterCodexSession = async () => {
        if (!currentTask.value?.task_id) return null
        closeMsgStream()
        const resp = await proxy.$api.starter
            .starterCodexSessionReset(currentTask.value.task_id)
            .then(async (res) => {
                if (res.code === 200) {
                    taskMessages.value = []
                    msgStreamModel.value.status = 'stale'
                    msgStreamModel.value.loading = false
                    msgStreamModel.value.msgs = []
                    msgEventKeys.value = new Set()
                    await getMessages()
                }
                return res
            })
            .catch((error) => {
                msgStreamModel.value.loading = false
                throw error
            })
        return resp
    }

    const terminateStarterCodexSession = async () => {
        if (!currentTask.value?.task_id) return null
        const resp = await proxy.$api.starter.starterCodexSessionTerminate(currentTask.value.task_id).then(async res => {
            if (res.code === 200) {
                closeMsgStream()
            }
            return res
        })
        return resp;
    }

    const closeMsgStream = ({ keepMessage = false } = {}) => {
        if (msgEventSource.value) {
            msgEventSource.value.close()
            msgEventSource.value = null
        }
        msgStreamModel.value.loading = false
        if (!keepMessage) {
            msgStreamModel.value.msgs = []
            msgEventKeys.value = new Set()
        }
    }
    const buildStreamEventKey = (data, event) => {
        const sessionId = data?.session_id || currentTask.value?.task_id || ''
        const eventIndex = data?._event_index ?? event?.lastEventId
        if (eventIndex !== undefined && eventIndex !== null && String(eventIndex) !== '') {
            return `${sessionId}:event-index:${eventIndex}`
        }
        const codexEvent = data?.event || {}
        const item = codexEvent?.item || {}
        const itemId = item?.id || item?.call_id || item?.thread_id
        if (data?.type === 'event' && codexEvent?.type && itemId) {
            return `${sessionId}:item:${codexEvent.type}:${itemId}`
        }
        if (data?.type === 'event' && codexEvent?.type && item?.type === 'command_execution' && item?.command) {
            return `${sessionId}:command:${codexEvent.type}:${item.command}`
        }
        if (data?.type && data?.message) return `${sessionId}:message:${data.type}:${data.message}`
        return null
    }
    const getMsgStream = async () => {
        if (!currentTask.value?.task_id) return
        closeMsgStream()
        let baseURL = getBaseURL()
        msgStreamModel.value.loading = true
        msgEventSource.value = new EventSource(
            `${baseURL}/starter/codex/session/${currentTask.value.task_id}/stream`
        )
        msgEventSource.value.onmessage = async (event) => {
            let resData = JSON.parse(event.data)
            if (resData.code !== 200) {
                msgStreamModel.value.loading = false
                msgStreamModel.value.msgs = []
                proxy.$barWarning(resData.message, {
                    status: 'warning'
                })
                return
            }
            const eventKey = buildStreamEventKey(resData.data, event)
            if (eventKey) {
                if (msgEventKeys.value.has(eventKey)) return
                msgEventKeys.value.add(eventKey)
            }
            msgStreamModel.value.msgs.push(resData.data)
        }
        msgEventSource.value.onerror = () => {
            closeMsgStream()
        }
    }
    const currentMsg = computed(() => {
        let lastIdx = msgStreamModel.value.msgs.length - 1
        for (let i = lastIdx; i >= 0; i--) {
            if (msgStreamModel.value.msgs[i].type === 'submitted') {
                lastIdx = i
                break
            }
        }
        return msgStreamModel.value.msgs.slice(lastIdx)
    })

    const stateSchema = ref(null)
    const getStateSchema = async () => {
        await proxy.$api.config.getStateSchema().then((res) => {
            if (res.code === 200) {
                stateSchema.value = res.data || {}
            } else {
                stateSchema.value = {}
            }
        })
    }

    const getTaskStateConfig = async (task_id = currentTask.value?.task_id) => {
        if (!task_id) return null
        const resp = await proxy.$api.task.getTaskStateConfig(task_id)
        return resp
    }
    const updateTaskStateConfig = async (payload, task_id = currentTask.value?.task_id) => {
        if (!task_id) return null
        const resp = await proxy.$api.task.updateTaskStateConfig(task_id, payload)
        return resp
    }

    return {
        configId,
        config,
        getConfigs,
        currentTask,
        setCurrentTask,
        resources,
        getResources,
        tasks,
        getTasks,
        resumeTask,
        createTask,
        renameTask,
        deleteTask,
        taskStatus,
        getStatus,
        taskMessages,
        msgStreamModel,
        looperTakeover,
        currentMsg,
        getMessages,
        resetStarterCodexSession,
        terminateStarterCodexSession,
        getMsgStream,
        clearLooperTakeoverCountdown,
        setLooperTakeoverCountdown,
        stateSchema,
        getStateSchema,
        getTaskStateConfig,
        updateTaskStateConfig
    }
})
