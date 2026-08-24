import axios from '../axios/config.js'
import { api } from '../axios/index.js'

export async function fetchTaskStatus(taskId) {
  return api.starter.getAgentStatus(taskId)
}

export async function fetchTaskSession(taskId) {
  return api.starter.starterCodexSession(taskId)
}

export async function submitTaskQuery(payload) {
  return api.starter.starterCodexStream(payload)
}

export async function runTaskLooper(taskId, payload = {}) {
  const response = await axios({
    method: 'post',
    url: `/starter/codex/session/${taskId}/looper`,
    data: payload,
    headers: {
      'Content-Type': 'application/json'
    },
    timeout: 120000
  })
  return response.data
}

export async function terminateTaskLooper(taskId) {
  return api.starter.starterCodexSessionLooperTerminate(taskId)
}

export async function resetTaskSession(taskId) {
  return api.starter.starterCodexSessionReset(taskId)
}

export async function terminateTaskSession(taskId) {
  return api.starter.starterCodexSessionTerminate(taskId)
}
