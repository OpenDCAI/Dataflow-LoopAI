<script setup>
import { TBox } from '@simon_he/vue-tui/vue'
import { TVirtualMarkdown } from '@simon_he/vue-tui/markdown'

const props = defineProps({
  x: { type: Number, required: true },
  y: { type: Number, required: true },
  w: { type: Number, required: true },
  h: { type: Number, required: true },
  active: { type: Boolean, required: true },
  content: { type: String, required: true },
  scrollTop: { type: Number, required: true }
})

const emit = defineEmits(['update:scrollTop'])
</script>

<template>
  <TBox :x="x" :y="y" :w="w" :h="h" border title="Transcript" :style="{ fg: active ? 'greenBright' : 'magentaBright' }">
    <TVirtualMarkdown
      :x="1"
      :y="1"
      :w="Math.max(8, w - 4)"
      :h="Math.max(1, h - 2)"
      :content="content"
      :scroll-top="scrollTop"
      :style="{ fg: 'whiteBright' }"
      selectable
      @update:scroll-top="(value) => emit('update:scrollTop', value)"
    />
  </TBox>
</template>
