<script setup>
import { TBox, TText } from '@simon_he/vue-tui/vue'

defineProps({
  cols: { type: Number, required: true },
  page: { type: String, required: true },
  tasksCount: { type: Number, required: true },
  loading: { type: Boolean, required: true },
  apiBaseUrl: { type: String, required: true },
  lastRefreshAt: { type: String, default: '-' }
})
</script>

<template>
  <TBox :x="0" :y="0" :w="cols" :h="3" border title="LoopAI TUI" :style="{ fg: 'cyanBright' }">
    <TText :x="1" :y="0" :w="Math.max(8, cols - 4)" :value="`/${page}   tasks=${tasksCount}   loading=${loading ? 'yes' : 'no'}`" />
    <TText :x="1" :y="1" :w="Math.max(8, cols - 4)" :value="`api=${apiBaseUrl}   updated=${lastRefreshAt || '-'}`" :style="{ fg: 'white' }" />
  </TBox>
</template>
