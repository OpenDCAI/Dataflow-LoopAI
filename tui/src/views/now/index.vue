<script setup>
import { Box, Text } from '@vue-tui/runtime'
import { useAppConfig } from '../../stores/appConfig.js'
import { useLoopAI } from '../../stores/loopAI.js'
import { useNowView } from '../../hooks/now/useNowView.js'

const appConfig = useAppConfig()
const loopAI = useLoopAI()
const {
  focusedNode,
  nodeHint,
  nodePaneHeight,
  chatPaneHeight,
  paneBorderColor,
  stateViewport,
  customViewport,
  userLines,
  toolViewport,
  assistantViewport,
  assistantWidth
} = useNowView()
</script>

<template>
  <Box marginTop="1" borderStyle="round" padding="1" flexDirection="column">
    <Text>{{ appConfig.local('Task') }}: {{ loopAI.currentTask?.name || loopAI.currentTask?.task_id || '-' }}</Text>
    <Text>id: {{ loopAI.currentTask?.task_id || '-' }}</Text>
    <Text>{{ appConfig.local('Status') }}: {{ loopAI.sessionStatus }}</Text>
    <Text>{{ appConfig.local('updated') }}: {{ loopAI.lastRefreshAt || '-' }}</Text>
    <Text></Text>
    <Text bold color="yellow">{{ focusedNode?.label || appConfig.local('Nodes') }} · {{ focusedNode?.running ? 'RUNNING' : focusedNode?.runtimeStatus || '-' }}</Text>
    <Text dimColor>{{ nodeHint }}</Text>
    <Box flexDirection="row" columnGap="1">
      <Box :width="assistantWidth / 2" :minHeight="nodePaneHeight + 2" :maxHeight="nodePaneHeight + 2" borderStyle="round" :borderColor="paneBorderColor.state" padding="1" flexDirection="column" overflow="hidden">
        <Text bold color="cyan">{{ appConfig.local('state') }} · {{ stateViewport.summary }}</Text>
        <Text v-for="(line, index) in stateViewport.lines" :key="`state-${index}-${stateViewport.safeOffset}`" wrap="truncate-end">{{ line }}</Text>
      </Box>
      <Box :width="assistantWidth / 2" :minHeight="nodePaneHeight + 2" :maxHeight="nodePaneHeight + 2" borderStyle="round" :borderColor="paneBorderColor.custom" padding="1" flexDirection="column" overflow="hidden">
        <Text bold color="cyan">{{ appConfig.local('custom_info') }} · {{ customViewport.summary }}</Text>
        <Text v-for="(line, index) in customViewport.lines" :key="`custom-${index}-${customViewport.safeOffset}`" wrap="truncate-end">{{ line }}</Text>
      </Box>
    </Box>
    <Box marginTop="1" borderStyle="round" borderColor="cyan" padding="1" flexDirection="column" :minHeight="4" :maxHeight="4" overflow="hidden">
      <Text bold color="cyan">User</Text>
      <Text v-for="(line, index) in userLines.slice(0, 2)" :key="`user-${index}`" color="cyan" wrap="truncate-end">{{ line }}</Text>
    </Box>
    <Box marginTop="1" borderStyle="round" :borderColor="paneBorderColor.tool" padding="1" flexDirection="column" :minHeight="6" :maxHeight="6" overflow="hidden">
      <Text bold color="yellow">Tool / Events · {{ toolViewport.summary }}</Text>
      <Text v-for="(line, index) in toolViewport.lines" :key="`tool-${index}-${toolViewport.safeOffset}`" color="yellow" wrap="truncate-end">{{ line }}</Text>
    </Box>
    <Box marginTop="1" borderStyle="round" :borderColor="paneBorderColor.assistant" padding="1" flexDirection="column" :minHeight="chatPaneHeight" :maxHeight="chatPaneHeight" overflow="hidden">
      <Text bold color="green">Assistant · {{ assistantViewport.summary }}</Text>
      <Text v-for="(line, index) in assistantViewport.lines" :key="`assistant-${index}-${assistantViewport.safeOffset}`" color="green" wrap="truncate-end">{{ line }}</Text>
    </Box>
  </Box>
</template>
