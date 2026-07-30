<script setup>
import { TBox, TText } from '@simon_he/vue-tui/vue'

defineProps({
  x: { type: Number, required: true },
  y: { type: Number, required: true },
  w: { type: Number, required: true },
  h: { type: Number, required: true },
  currentTaskName: { type: String, required: true },
  currentTaskId: { type: String, required: true },
  status: { type: String, required: true },
  nodeCount: { type: Number, required: true }
})
</script>

<template>
  <TBox :x="x" :y="y" :w="w" :h="h" border title="Task Summary" :style="{ fg: 'cyanBright' }">
    <TText :x="1" :y="0" :w="Math.max(12, w - 6)" :value="`任务: ${currentTaskName}`" :style="{ fg: 'greenBright', bold: true }" />
    <TText :x="1" :y="1" :w="Math.max(12, w - 6)" :value="`id: ${currentTaskId}   状态: ${status}   节点: ${nodeCount}`" />
  </TBox>
</template>
