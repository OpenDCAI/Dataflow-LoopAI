import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { getCurrentInstance } from 'vue'

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

    return {
        configId,
        config,
        getConfigs,
        tasks,
        getTasks
    }
})