<script setup>
import { computed } from 'vue'
import { Box, Text } from '@vue-tui/runtime'
import { useAppConfig } from '../../stores/appConfig.js'
import { useLoopAI } from '../../stores/loopAI.js'

const appConfig = useAppConfig()
const loopAI = useLoopAI()

const hint = computed(() => {
  if (appConfig.page === 'home') {
    return '/tasks  /now  /quit'
  }
  if (appConfig.page === 'tasks') {
    return '/now  /new <name>  /rename <name>  /delete  /refresh  /quit'
  }
  return '/tasks  /refresh  /quit  h/l or ←/→ nodes  j/k logs  type query and Enter'
})
const footerStatus = computed(() => (loopAI.loading ? '[loading]' : `[${loopAI.toast}]`))
</script>

<template>
  <Box marginTop="1" borderStyle="round" padding="1" flexDirection="column">
    <Text dimColor>{{ hint }}</Text>
    <Text color="green">> {{ loopAI.inputBuffer }}</Text>
    <Text dimColor>{{ footerStatus }}</Text>
  </Box>
</template>
