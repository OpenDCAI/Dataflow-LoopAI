import axios from 'axios'

const DEFAULT_BASE_URL = import.meta.env.VITE_LOOPAI_API_BASE_URL || 'http://127.0.0.1:8855'

const client = axios.create({
  baseURL: DEFAULT_BASE_URL,
  timeout: 15000
})

export const getBaseURL = () => client.defaults.baseURL

export default client
