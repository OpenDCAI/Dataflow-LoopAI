import { api } from '../axios/index.js'

export async function fetchTasks() {
  return api.task.getTasks()
}

export async function createTask(payload) {
  return api.task.createTask(payload)
}

export async function updateTask(payload) {
  return api.task.updateTask(payload)
}

export async function deleteTask(id) {
  return api.task.delTask(id)
}
