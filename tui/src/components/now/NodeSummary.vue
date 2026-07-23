<script setup>
import { Box, Text } from '@vue-tui/runtime'
import { useAppConfig } from '../../stores/appConfig.js'
import { useLoopAI } from '../../stores/loopAI.js'

const appConfig = useAppConfig()
const loopAI = useLoopAI()
</script>

<template>
  <Box flexDirection="column">
    <Text bold color="yellow">{{ appConfig.local('Nodes') }} ({{ loopAI.nodeCards.length }})</Text>
    <Text>------------</Text>
    <template v-if="loopAI.nodeCards.length === 0">
      <Text dimColor>{{ appConfig.local('No node data available for this task yet.') }}</Text>
    </template>
    <template v-else>
      <Box v-for="card in loopAI.nodeCards" :key="card.key" flexDirection="column" marginBottom="1">
        <Text bold>{{ card.label }} · {{ card.running ? 'RUNNING' : card.runtimeStatus }}</Text>
        <Text v-for="(line, idx) in card.stateLines.slice(0, 3)" :key="`${card.key}-state-${idx}`" wrap="truncate-end">
          {{ appConfig.local('state') }}: {{ line }}
        </Text>
        <Text v-for="(line, idx) in card.customLines.slice(0, 2)" :key="`${card.key}-custom-${idx}`" wrap="truncate-end">
          {{ appConfig.local('custom_info') }}: {{ line }}
        </Text>
      </Box>
    </template>
  </Box>
</template>
