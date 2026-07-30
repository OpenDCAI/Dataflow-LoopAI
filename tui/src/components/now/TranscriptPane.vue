<script setup>
import { TBox, TText } from '@simon_he/vue-tui/vue'

defineProps({
  x: { type: Number, required: true },
  y: { type: Number, required: true },
  w: { type: Number, required: true },
  h: { type: Number, required: true },
  nowActivePane: { type: String, required: true },
  userPreviewLines: { type: Array, required: true },
  eventViewport: { type: Array, required: true },
  transcriptViewport: { type: Array, required: true }
})
</script>

<template>
  <TBox :x="x" :y="y" :w="Math.floor(w * 0.3)" :h="h" border title="User" :style="{ fg: 'cyanBright' }">
    <TText
      v-for="(line, index) in userPreviewLines"
      :key="`user-preview-${index}`"
      :x="1"
      :y="1 + index"
      :w="Math.max(8, Math.floor(w * 0.3) - 4)"
      :value="line"
      :style="{ fg: 'cyanBright' }"
    />
  </TBox>

  <TBox
    :x="x + Math.floor(w * 0.3)"
    :y="y"
    :w="Math.floor(w * 0.3)"
    :h="h"
    border
    title="Runtime"
    :style="{ fg: nowActivePane === 'tool' ? 'yellowBright' : 'whiteBright' }"
  >
    <TText
      v-for="(line, index) in eventViewport"
      :key="`event-line-${index}`"
      :x="1"
      :y="1 + index"
      :w="Math.max(8, Math.floor(w * 0.3) - 4)"
      :value="line.text"
      :style="line.style"
    />
  </TBox>

  <TBox
    :x="x + Math.floor(w * 0.6)"
    :y="y"
    :w="w - Math.floor(w * 0.6)"
    :h="h"
    border
    title="Transcript"
    :style="{ fg: nowActivePane === 'assistant' ? 'greenBright' : 'magentaBright' }"
  >
    <TText
      v-for="(line, index) in transcriptViewport"
      :key="`transcript-line-${index}`"
      :x="1"
      :y="1 + index"
      :w="Math.max(8, w - Math.floor(w * 0.6) - 4)"
      :value="line.text"
      :style="line.style"
    />
  </TBox>
</template>
