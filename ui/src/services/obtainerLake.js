import axios from '@/axios/config'

export async function getDataMixerMonitor(params = {}) {
    const res = await axios.get('/obtainer/lake/monitor', { params })
    return res.data
}

export async function getWebAgentOverview(params = {}) {
    const res = await axios.get('/obtainer/webagent/overview', { params })
    return res.data
}

export async function rebuildDataMixerMonitor(params = {}) {
    const res = await axios.post('/obtainer/lake/monitor/rebuild', null, { params })
    return res.data
}

export async function getDataMixerEmbeddingHealth(params = {}) {
    const res = await axios.get('/obtainer/lake/embedding_health', { params })
    return res.data
}

export async function getDataMixerCommands() {
    const res = await axios.get('/obtainer/datamixer/commands')
    return res.data
}

export async function runDataMixerCli(payload = {}) {
    const res = await axios.post('/obtainer/datamixer/cli', payload)
    return res.data
}

export async function getDataMixerLakeCurrent(params = {}) {
    const res = await axios.get('/obtainer/datamixer/lake/current', { params })
    return res.data
}

export async function scanDataMixerLakes(params = {}) {
    const res = await axios.get('/obtainer/datamixer/lake/scan', { params })
    return res.data
}

export async function loadDataMixerLake(payload = {}) {
    const res = await axios.post('/obtainer/datamixer/lake/load', payload)
    return res.data
}

export async function deleteDataMixerLake(payload = {}) {
    const res = await axios.post('/obtainer/datamixer/lake/delete', payload)
    return res.data
}

export const getLakeMonitor = getDataMixerMonitor
export const getEmbeddingHealth = getDataMixerEmbeddingHealth
