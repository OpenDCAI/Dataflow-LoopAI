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

    const taskStatus = ref({
        started: false,
        running: false,
        waiting_llm: true,
        event_streaming: 'not_ready',
        current: null,
        running_tasks: null,
        interrupt_value: 'input the human query',
        state: null,
        custom_info: null,
        update_custom_info: null
    })
    const getStatus = async (task_id = currentTask.value?.task_id) => {
        if (!task_id) return
        await proxy.$api.starter
            .getAgentStatus(task_id)
            .then((res) => {
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
            .catch((error) => {
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
        loading: false
    })
    const msgEventSource = ref(null)


    const setCurrentTask = async (task) => {
        currentTask.value = task || null
        closeMsgStream()
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
                    msgStreamModel.value.loading = res?.data?.status !== 'completed'
                }
            })
            .catch(() => {
                msgStreamModel.value.loading = false
            })
    }

    const closeMsgStream = ({ keepMessage = false } = {}) => {
        if (msgEventSource.value) {
            msgEventSource.value.close()
            msgEventSource.value = null
        }
        msgStreamModel.value.loading = false
        if (!keepMessage) msgStreamModel.value.msgs = []
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
        currentMsg,
        getMessages,
        getMsgStream,
        stateSchema,
        getStateSchema
    }
})
