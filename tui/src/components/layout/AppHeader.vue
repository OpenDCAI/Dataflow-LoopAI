<script setup>
import { computed } from 'vue'
import { Box, Spacer, Text } from '@vue-tui/runtime'
import { useAppConfig } from '../../stores/appConfig.js'
import { useLoopAI } from '../../stores/loopAI.js'

const appConfig = useAppConfig()
const loopAI = useLoopAI()

const title = computed(() => {
  if (appConfig.page === 'home') return '/home'
  return appConfig.page === 'tasks' ? '/tasks' : '/now'
})
const subtitle = computed(() => {
  if (appConfig.page === 'home') {
    return `LoopAI TUI  ${appConfig.local('Tasks')}=${loopAI.tasks.length}`
  }
  if (appConfig.page === 'tasks') {
    return `${appConfig.local('Tasks')}=${loopAI.tasks.length}`
  }
  return `${appConfig.local('Task')}=${loopAI.currentTask?.name || '-'}  session=${loopAI.sessionStatus}`
})
const apiBaseUrl = import.meta.env.VITE_LOOPAI_API_BASE_URL || 'http://127.0.0.1:8855'
</script>

<template>
  <Box borderStyle="round" padding="1">
    <Text bold color="cyan">{{ title }}</Text>
    <Text>  {{ subtitle }}</Text>
    <Spacer />
    <Text dimColor>api={{ apiBaseUrl }}</Text>
  </Box>
</template>
