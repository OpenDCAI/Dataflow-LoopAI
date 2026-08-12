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

    const tasks = ref([])
    const getTasks = async () => {
        await proxy.$api.task.getTasks().then((res) => {
            if (res.code === 200) {
                let _tasks = res.data || []
                _tasks.forEach((item) => {
                    item.show = true
                })
                tasks.value = _tasks
            } else {
                proxy.$barWarning(res.message, {
                    status: 'warning'
                })
            }
        })
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
    const processSteps = ref([])
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
                    // Polling-based execution process: derive readable steps from
                    // the session events the frontend already fetches, so the
                    // starter's actions stay visible even if the SSE stream is
                    // unavailable.
                    const steps = []
                    for (const entry of res.data?.events || []) {
                        const payload = entry?.payload || {}
                        if (payload.type !== 'event') continue
                        const event = payload.event || {}
                        if (event.type !== 'item.completed') continue
                        const item = event.item || {}
                        if (item.type === 'agent_message' && item.text) {
                            steps.push({ type: 'assistant', text: item.text })
                        } else if (item.type === 'command_execution' && item.command) {
                            steps.push({ type: 'tool', text: item.command })
                        } else if (item.type === 'todo_list') {
                            steps.push({ type: 'todo', items: item.items || [] })
                        }
                    }
                    processSteps.value = steps.slice(-400)
                    const streamStatus = res?.data?.status || 'stale'
                    const shouldStream = ['submitted', 'running', 'finishing'].includes(streamStatus)
                    msgStreamModel.value.status = streamStatus
                    msgStreamModel.value.loading = shouldStream
                    if (shouldStream && !msgEventSource.value) {
                        getMsgStream()
                    } else if (!shouldStream && msgStreamModel.value.msgs.length > 0) {
                        // Session finished: fold the streamed execution process
                        // into the conversation so the starter's steps stay visible.
                        // Only fold process steps (commands / todo) - assistant text
                        // is already in the conversation and would duplicate.
                        const folded = msgStreamModel.value.msgs
                            .filter((m) => m && m.type === 'event' && m.event && m.event.item)
                            .map((m) => {
                                const item = m.event.item
                                if (item.type === 'todo_list') {
                                    const lines = (item.items || []).map((i) => `- [${i.completed ? 'x' : ' '}] ${i.text}`)
                                    return { type: 'assistant', data: { content: lines.join('\n'), process: true } }
                                }
                                if (item.type === 'command_execution' && m.event.type === 'item.completed') {
                                    return { type: 'tool', data: { content: item.command || '', process: true } }
                                }
                                return null
                            })
                            .filter(Boolean)
                        if (folded.length) {
                            taskMessages.value = [...taskMessages.value, ...folded]
                        }
                        msgStreamModel.value.msgs = []
                        msgEventKeys.value = new Set()
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
        let baseURL = (getBaseURL() || '').replace(/\/+$/, '')
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
        taskStatus,
        getStatus,
        taskMessages,
        msgStreamModel,
        processSteps,
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
