import { onBeforeUnmount, onMounted } from 'vue'
import { useApp, useInput } from '@vue-tui/runtime'
import { storeToRefs } from 'pinia'
import { useAppConfig } from '../../stores/appConfig.js'
import { useLoopAI } from '../../stores/loopAI.js'

const POLL_MS = 3000

export function useAppShell() {
  const app = useApp()
  const appConfig = useAppConfig()
  const loopAI = useLoopAI()
  const { page, inputBuffer } = storeToRefs(loopAI)

  let pollTimer = null

  useInput(async (input, key) => {
    try {
      if (key.ctrl && input === 'c') {
        app.exit()
        return
      }
      if (key.downArrow || (input === 'j' && inputBuffer.value.length === 0)) {
        if (page.value === 'tasks') await loopAI.selectTaskByDelta(1)
        else loopAI.scrollBy(1)
        return
      }
      if (key.upArrow || (input === 'k' && inputBuffer.value.length === 0)) {
        if (page.value === 'tasks') await loopAI.selectTaskByDelta(-1)
        else loopAI.scrollBy(-1)
        return
      }
      if ((key.leftArrow || input === 'h') && page.value === 'now' && inputBuffer.value.length === 0) {
        loopAI.scrollNodeBoardBy(-1)
        return
      }
      if ((key.rightArrow || input === 'l') && page.value === 'now' && inputBuffer.value.length === 0) {
        loopAI.scrollNodeBoardBy(1)
        return
      }
      if (key.pageDown) {
        loopAI.scrollBy(10)
        return
      }
      if (key.pageUp) {
        loopAI.scrollBy(-10)
        return
      }
      if (key.escape) {
        loopAI.clearInput()
        loopAI.setToast(appConfig.local('Cancelled'))
        return
      }
      if (key.return) {
        const command = inputBuffer.value.trim()
        if (command === '/quit') {
          app.exit()
          return
        }
        if (page.value === 'tasks' && command === '') {
          await loopAI.activateSelectedTask()
          return
        }
        await loopAI.submitInput()
        return
      }
      if (key.backspace) {
        loopAI.backspaceInput()
        return
      }
      if (input) {
        loopAI.appendInput(input)
      }
    } catch (error) {
      loopAI.setToast(error?.message || String(error))
    }
  })

  onMounted(async () => {
    try {
      await loopAI.bootstrap()
      pollTimer = setInterval(() => {
        loopAI.refreshCurrent(true).catch((error) => {
          loopAI.setToast(error?.message || String(error))
        })
      }, POLL_MS)
    } catch (error) {
      loopAI.setToast(error?.message || String(error))
    }
  })

  onBeforeUnmount(() => {
    if (pollTimer) clearInterval(pollTimer)
  })
}
