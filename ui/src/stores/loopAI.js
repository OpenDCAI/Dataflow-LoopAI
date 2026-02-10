import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { getCurrentInstance } from 'vue'

import { getBaseURL } from '@/axios/config'

export const useLoopAI = defineStore('useLoopAI', () => {
    const instance = getCurrentInstance()
    const proxy = instance.proxy

    const configId = ref(null)
    const config = ref({
        system: {},
        states: {}
    })
    const getConfigs = async () => {
        await proxy.$api.config.getConfig().then((res) => {
            if (res.data) {
                configId.value = res.data.id
                let { system, states } = res.data
                config.value.system = system
                config.value.states = states
            }
        })
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
    const getStatus = async () => {
        await proxy.$api.starter
            .getAgentStatus()
            .then((res) => {
                if (res.code === 200) {
                    taskStatus.value.started = true
                    for (let key in taskStatus.value) {
                        if (res.data[key] !== undefined) taskStatus.value[key] = res.data[key]
                    }
                    syncMessages()
                    checkIfMsgStreamOnGoing()
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
    // sometimes when the llm call the tools, the msg stream will receive the finished status, but it actually still not finished.
    // we should check if the msg stream is on going.
    const checkIfMsgStreamOnGoing = async () => {
        if (taskStatus.value.event_streaming === 'start' && !msgStreamModel.value.loading)
            await getMsgStream()
    }
    const taskMessages = ref([])
    const msgStreamModel = ref({
        msg: null,
        loading: false
    })
    const msgEventSource = ref(null)
    const getMessages = async () => {
        const getMsg = async () => {
            await proxy.$api.starter.getAgentMessages().then((res) => {
                if (res.code === 200) {
                    taskMessages.value = res.data || []
                } else {
                    taskMessages.value = []
                }
            })
        }
        if (taskStatus.value.waiting_llm)
            try {
                taskMessages.value = taskStatus.value.custom_info.llm_node.data.history
            } catch (e) {
                await getMsg()
            }
        else {
            await getMsg()
        }
    }
    const syncMessages = () => {
        const getMsg = () => {
            try {
                let _messages = []
                taskStatus.value.state.messages.forEach((item, index) => {
                    _messages.push({
                        type: item.type,
                        data: item
                    })
                })
                taskMessages.value = _messages
            } catch (e) {
                taskMessages.value = []
            }
        }
        if (taskStatus.value.waiting_llm)
            try {
                taskMessages.value = taskStatus.value.custom_info.llm_node.data.history
            } catch (e) {
                getMsg()
            }
        else getMsg()
    }
    const getMsgStream = async () => {
        if (msgEventSource.value) {
            msgEventSource.value.close()
        }
        let baseURL = getBaseURL()
        msgEventSource.value = new EventSource(baseURL + '/starter/agent/message/stream')
        msgEventSource.value.onmessage = async (event) => {
            let resData = JSON.parse(event.data)
            if (resData.code === 200) {
                if (resData.status === 'loading') {
                    msgStreamModel.value.loading = true
                    msgStreamModel.value.msg = resData.data
                } else if (resData.status === 'success') {
                    msgStreamModel.value.loading = false
                    msgStreamModel.value.msg = null
                    await getStatus()
                }
            } else if (resData.code === 400) {
                msgStreamModel.value.loading = false
                msgStreamModel.value.msg = null
                proxy.$barWarning(resData.message, {
                    status: 'warning'
                })
            }
        }
        msgEventSource.value.onerror = (error) => {
            // console.error(error)
            msgEventSource.value.close()
            msgStreamModel.value.loading = false
            msgStreamModel.value.msg = null
        }
    }

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
        resources,
        getResources,
        tasks,
        getTasks,
        taskStatus,
        getStatus,
        taskMessages,
        msgStreamModel,
        getMessages,
        getMsgStream,
        stateSchema,
        getStateSchema
    }
})
