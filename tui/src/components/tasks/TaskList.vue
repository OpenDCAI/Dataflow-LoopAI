<script setup>
import { Box, Newline, Text } from '@vue-tui/runtime'
import { useAppConfig } from '../../stores/appConfig.js'
import { useLoopAI } from '../../stores/loopAI.js'
import { formatTime } from '../../lib/format.js'
import { useTasksView } from '../../hooks/tasks/useTasksView.js'

const appConfig = useAppConfig()
const loopAI = useLoopAI()
const { title, visibleTasks, footerHint } = useTasksView()
</script>

<template>
  <Box borderStyle="round" padding="1" flexDirection="column">
    <Text bold color="yellow">{{ title }}</Text>
    <Newline />
    <template v-if="loopAI.tasks.length === 0">
      <Text dimColor>{{ appConfig.local('No task yet. Press n to create one.') }}</Text>
      <Text>/new &lt;name&gt;</Text>
    </template>
    <template v-else>
      <Box v-for="item in visibleTasks" :key="item.task.task_id" flexDirection="column" marginBottom="1">
        <Text :inverse="item.actualIndex === loopAI.selectedTaskIndex" :bold="item.actualIndex === loopAI.selectedTaskIndex" color="green" wrap="truncate-end">
          {{ `${item.actualIndex === loopAI.selectedTaskIndex ? '>' : ' '} ${item.task.name || item.task.task_id || '(unnamed)'}` }}
        </Text>
        <Text dimColor wrap="truncate-end">id: {{ item.task.task_id }}</Text>
        <Text dimColor wrap="truncate-end">{{ appConfig.local('updated') }}: {{ formatTime(item.task.updatedAt) }}</Text>
      </Box>
      <Text dimColor>{{ footerHint }}</Text>
      <Text dimColor>Enter = activate task</Text>
    </template>
  </Box>
</template>
