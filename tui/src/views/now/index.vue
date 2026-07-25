<script setup>
import { Box, Text } from '@vue-tui/runtime'
import { useAppConfig } from '../../stores/appConfig.js'
import { useLoopAI } from '../../stores/loopAI.js'
import { useNowView } from '../../hooks/now/useNowView.js'

const appConfig = useAppConfig()
const loopAI = useLoopAI()
const { nodeBoardLines, nodeHint, transcriptHeight, transcriptRenderItems } = useNowView()
</script>

<template>
  <Box marginTop="1" borderStyle="round" padding="1" flexDirection="column">
    <Text>{{ appConfig.local('Task') }}: {{ loopAI.currentTask?.name || loopAI.currentTask?.task_id || '-' }}</Text>
    <Text>id: {{ loopAI.currentTask?.task_id || '-' }}</Text>
    <Text>{{ appConfig.local('Status') }}: {{ loopAI.sessionStatus }}</Text>
    <Text>{{ appConfig.local('updated') }}: {{ loopAI.lastRefreshAt || '-' }}</Text>
    <Text></Text>
    <Text bold color="yellow">{{ appConfig.local('Nodes') }} ({{ loopAI.nodeCards.length }})</Text>
    <Text dimColor>{{ nodeHint }}</Text>
    <Text v-for="(line, index) in nodeBoardLines" :key="`node-board-${index}-${loopAI.nodeScrollOffset}`" wrap="truncate-end">
      {{ line }}
    </Text>
    <Box marginTop="1" borderStyle="round" borderColor="cyan" padding="1" flexDirection="column" :minHeight="transcriptHeight" :maxHeight="transcriptHeight" overflow="hidden">
      <Text bold color="magenta">{{ appConfig.local('Conversation') }} / {{ appConfig.local('Runtime Events') }}</Text>
      <Text v-for="item in transcriptRenderItems" :key="item.key" :color="item.color" :dimColor="item.dim" wrap="truncate-end">
        {{ item.text }}
      </Text>
    </Box>
  </Box>
</template>
