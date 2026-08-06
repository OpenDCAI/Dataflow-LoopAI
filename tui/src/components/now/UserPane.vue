<script setup>
import { TBox } from '@simon_he/vue-tui/vue'
import { TMarkdownText } from '@simon_he/vue-tui/markdown'

defineProps({
  x: { type: Number, required: true },
  y: { type: Number, required: true },
  w: { type: Number, required: true },
  h: { type: Number, required: true },
  content: { type: String, required: true }
})
</script>

<template>
  <TBox :x="x" :y="y" :w="w" :h="h" border title="User" :style="{ fg: 'cyanBright' }">
    <TMarkdownText :x="1" :y="1" :w="Math.max(8, w - 4)" :h="Math.max(1, h - 2)" :content="content" :style="{ fg: 'cyanBright' }" />
  </TBox>
</template>
