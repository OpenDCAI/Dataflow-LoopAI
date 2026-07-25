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
