import axios from '@/axios/config'

export async function getLakeMonitor(params = {}) {
    const res = await axios.get('/obtainer/lake/monitor', { params })
    return res.data
}

export async function getEmbeddingHealth(params = {}) {
    const res = await axios.get('/obtainer/lake/embedding_health', { params })
    return res.data
}
