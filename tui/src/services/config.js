import { api } from '../axios/index.js'

export async function fetchConfig() {
  return api.config.getConfig()
}
