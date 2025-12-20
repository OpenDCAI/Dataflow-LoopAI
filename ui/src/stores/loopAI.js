import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { getCurrentInstance } from 'vue'

import { getBaseURL } from '@/axios/config'

export const useLoopAI = defineStore('useLoopAI', () => {
    const instance = getCurrentInstance()
    const proxy = instance.proxy

    const configId = ref(null)
    const config = ref({})
    const getConfigs = async () => {
        await proxy.$api.config.getConfig().then((res) => {
            if (res.data) {
                configId.value = res.data.id
                let _config = res.data.config
                for (let key in _config) {
                    if (_config[key]) {
                        for (let param_key in _config[key]) {
                            _config[key][param_key].value =
                                _config[key][param_key].value === null
                                    ? ''
                                    : _config[key][param_key].value
                        }
                    }
                }
                config.value = _config
            }
        })
    }

    const tasks = ref([])
    const getTasks = async () => {
        await proxy.$api.task.getTasks().then((res) => {
            if (res.code === 200) {
                let _tasks = res.data || []
                _tasks.forEach((item) => {
                    item.show = true;
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
        current: null,
        interrupt_value: "input the human query",
        state: null
    })
    const getStatus = async () => {
        await proxy.$api.starter.getAgentStatus().then((res) => {
            if (res.code === 200) {
                taskStatus.value.started = true
                for (let key in taskStatus.value) {
                    if (res.data[key] !== undefined)
                        taskStatus.value[key] = res.data[key]
                }
            } else {
                taskStatus.value.started = false
                taskStatus.value.running = false
                taskStatus.value.waiting_llm = false
            }
        }).catch((error) => {
            taskStatus.value.running = false
            taskStatus.value.waiting_llm = false
            proxy.$barWarning('server connection error', {
                status: 'error'
            })
        })
    }
    const taskMessages = ref([])
    const msgStreamModel = ref({
        msg: null,
        loading: false,
    })
    const msgEventSource = ref(null)
    const getMessages = async () => {
        await proxy.$api.starter.getAgentMessages().then((res) => {
            if (res.code === 200) {
                taskMessages.value = res.data || []
            } else {
                taskMessages.value = []
            }
        })
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
                    await getMessages()
                    await getStatus()
                }
            }
            else if (resData.code === 400) {
                msgStreamModel.value.loading = false
                msgStreamModel.value.msg = null
                proxy.$barWarning(resData.message, {
                    status: 'warning'
                })
            }
        }
        msgEventSource.value.onerror = (error) => {
            console.error(error)
            msgEventSource.value.close()
            msgStreamModel.value.loading = false
            msgStreamModel.value.msg = null
        }
    }

    return {
        configId,
        config,
        getConfigs,
        tasks,
        getTasks,
        taskStatus,
        getStatus,
        taskMessages,
        msgStreamModel,
        getMessages,
        getMsgStream,
    }
})