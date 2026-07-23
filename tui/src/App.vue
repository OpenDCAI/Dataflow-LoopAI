<script setup>
import { Box } from '@vue-tui/runtime'
import { storeToRefs } from 'pinia'
import AppHeader from './components/layout/AppHeader.vue'
import AppFooter from './components/layout/AppFooter.vue'
import TasksView from './views/tasks/index.vue'
import NowView from './views/now/index.vue'
import { useAppConfig } from './stores/appConfig.js'
import { useAppShell } from './hooks/app/useAppShell.js'

const appConfig = useAppConfig()
const { page } = storeToRefs(appConfig)

useAppShell()
</script>

<template>
  <Box flexDirection="column" padding="0">
    <AppHeader />
    <TasksView v-if="page === 'tasks'" />
    <NowView v-else />
    <AppFooter />
  </Box>
</template>
