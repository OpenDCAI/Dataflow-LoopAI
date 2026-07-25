<script setup>
import { computed } from 'vue'
import { Box, Text } from '@vue-tui/runtime'
import { useAppConfig } from '../../stores/appConfig.js'
import { useLoopAI } from '../../stores/loopAI.js'
import { formatConversationLine, normalizeConversationItem, summarizeEvent } from '../../lib/messages.js'

const appConfig = useAppConfig()
const loopAI = useLoopAI()
const lines = computed(() => {
  const result = []
  result.push(`${appConfig.local('Conversation')}`)
  result.push('------------')
  if (!loopAI.conversation.length) {
    result.push(appConfig.local('empty'))
  } else {
    for (const item of loopAI.conversation) {
      result.push(...formatConversationLine(normalizeConversationItem(item), 100, appConfig.local))
      result.push('')
    }
  }
  result.push(`${appConfig.local('Runtime Events')}`)
  result.push('--------------')
  if (!loopAI.sessionEvents.length) {
    result.push(appConfig.local('empty'))
  } else {
    for (const event of loopAI.sessionEvents) {
      result.push(...summarizeEvent(event.payload || {}, appConfig.local))
      result.push('')
    }
  }
  return result
})
</script>

<template>
  <Box flexDirection="column">
    <Text v-for="(line, index) in lines" :key="`transcript-${index}`" wrap="truncate-end">
      {{ line }}
    </Text>
  </Box>
</template>
