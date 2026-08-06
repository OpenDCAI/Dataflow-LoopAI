<script setup>
import { TBox, TText } from '@simon_he/vue-tui/vue'
import LogoPixel from './LogoPixel.vue'

defineProps({
  x: { type: Number, required: true },
  y: { type: Number, required: true },
  w: { type: Number, required: true },
  h: { type: Number, required: true },
  helpLines: { type: Array, required: true }
})
</script>

<template>
  <TBox :x="x" :y="y" :w="w" :h="h" border title="Home" :style="{ fg: 'magentaBright' }">
    <LogoPixel :x="Math.max(2, Math.floor((w - 92) / 2))" :y="2" />
    <TText
      v-for="(line, index) in helpLines"
      :key="`home-help-${index}`"
      :x="Math.max(2, Math.floor((w - 96) / 2))"
      :y="11 + index * 2"
      :w="Math.min(96, w - 6)"
      :value="line"
      :style="index === 0 ? { fg: 'greenBright' } : { fg: 'whiteBright' }"
    />
  </TBox>
</template>
