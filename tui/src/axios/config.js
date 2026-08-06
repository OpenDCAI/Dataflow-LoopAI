import axios from 'axios'

const DEFAULT_BASE_URL = import.meta.env.VITE_LOOPAI_API_BASE_URL || 'http://127.0.0.1:8855'

const client = axios.create({
  baseURL: DEFAULT_BASE_URL,
  timeout: 60000
})

client.interceptors.request.use((config) => {
  const method = String(config?.method || 'get').toUpperCase()
  if (method === 'GET' || method === 'HEAD') {
    delete config.data
  }
  return config
})

export const getBaseURL = () => client.defaults.baseURL

export default client
