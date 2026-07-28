<script setup>
import { Box, Text } from '@vue-tui/runtime'
import { useAppConfig } from '../../stores/appConfig.js'
import { useLoopAI } from '../../stores/loopAI.js'
import LogoPixel from '../../components/home/LogoPixel.vue'

const appConfig = useAppConfig()
const loopAI = useLoopAI()
</script>

<template>
  <Box marginTop="1" borderStyle="round" borderColor="magenta" padding="1" flexDirection="column" alignItems="center">
    <LogoPixel />
    <Text></Text>
    <Text bold color="cyan">LoopAI TUI</Text>
    <Text dimColor>pixel splash inspired by ui/src/assets/logo/logo.svg</Text>
    <Text></Text>
    <Text>/tasks  {{ appConfig.local('Tasks') }} · browse and manage tasks</Text>
    <Text>/now    open current task, or fall back to the first task</Text>
    <Text>/quit   {{ appConfig.local('quit') }}</Text>
    <Text></Text>
    <Text dimColor>{{ appConfig.local('Tasks') }}={{ loopAI.tasks.length }}  current={{ loopAI.currentTask?.name || loopAI.currentTask?.task_id || '-' }}</Text>
  </Box>
</template>
