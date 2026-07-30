<script setup>
import { TBox, TText } from '@simon_he/vue-tui/vue'

defineProps({
  x: { type: Number, required: true },
  y: { type: Number, required: true },
  w: { type: Number, required: true },
  h: { type: Number, required: true },
  title: { type: String, required: true },
  visibleTasks: { type: Array, required: true },
  selectedTaskIndex: { type: Number, required: true },
  taskItemHeight: { type: Number, default: 3 },
  taskPage: { type: Number, required: true },
  taskPageCount: { type: Number, required: true },
  pageLabel: { type: String, required: true },
  emptyText: { type: String, required: true }
})
</script>

<template>
  <TBox :x="x" :y="y" :w="w" :h="h" border :title="title" :style="{ fg: 'yellowBright' }">
    <TText :x="1" :y="0" :w="Math.max(12, w - 6)" :value="'所有任务列表'" :style="{ fg: 'white' }" />
    <template v-if="visibleTasks.length">
      <template v-for="({ task, actualIndex }, index) in visibleTasks" :key="task.task_id || task.id || index">
        <TText
          :x="1"
          :y="1 + index * taskItemHeight"
          :w="Math.max(12, w - 6)"
          :value="`${actualIndex === selectedTaskIndex ? '▶' : ' '} ${task.name || task.task_id || '-'}`"
          :style="actualIndex === selectedTaskIndex ? { fg: 'greenBright', bold: true } : { fg: 'whiteBright' }"
        />
        <TText
          :x="3"
          :y="2 + index * taskItemHeight"
          :w="Math.max(12, w - 8)"
          :value="`id=${task.task_id || '-'}   updated=${task.updatedLabel || '-'}`"
          :style="{ fg: 'cyanBright' }"
        />
      </template>
    </template>
    <TText v-else :x="1" :y="1" :w="Math.max(12, w - 6)" :value="emptyText" :style="{ fg: 'redBright' }" />
    <TText :x="1" :y="Math.max(1, h - 3)" :w="Math.max(12, w - 6)" :value="`${pageLabel} ${taskPage}/${taskPageCount}   Enter = activate task`" :style="{ fg: 'white' }" />
  </TBox>
</template>
