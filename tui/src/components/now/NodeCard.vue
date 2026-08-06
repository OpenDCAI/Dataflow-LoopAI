<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { TBox, TText } from '@simon_he/vue-tui/vue'

const props = defineProps({
  card: { type: Object, required: true },
  index: { type: Number, required: true },
  x: { type: Number, required: true },
  y: { type: Number, required: true },
  w: { type: Number, required: true },
  h: { type: Number, required: true }
})

const blinkOn = ref(true)
let blinkTimer = null

const statusTone = computed(() => String(props.card?.runtimeStatus || 'idle').toLowerCase())

const titleStyle = computed(() => {
  if (statusTone.value === 'running') {
    return {
      fg: 'black',
      bg: blinkOn.value ? 'yellowBright' : '#f97316',
      bold: true
    }
  }
  if (statusTone.value === 'completed') {
    return { fg: 'black', bg: 'greenBright', bold: true }
  }
  if (statusTone.value === 'error' || statusTone.value === 'failed') {
    return { fg: 'whiteBright', bg: 'redBright', bold: true }
  }
  return { fg: 'whiteBright', bg: 'blueBright', bold: true }
})

onMounted(() => {
  if (statusTone.value !== 'running') return
  blinkTimer = setInterval(() => {
    blinkOn.value = !blinkOn.value
  }, 500)
})

onBeforeUnmount(() => {
  if (blinkTimer) clearInterval(blinkTimer)
})
</script>

<template>
  <TBox
    :x="x"
    :y="y"
    :w="w"
    :h="h"
    border
    :title="`${card.label} · ${card.runtimeStatus}`"
    :style="card.borderStyle"
    :title-style="titleStyle"
  >
    <TText :x="1" :y="0" :w="w - 4" :value="`updated: ${card.runtimeUpdatedLabel || '-'}`" :style="{ fg: 'white' }" />
    <TText :x="1" :y="1" :w="w - 4" :value="card.focusLabel" :style="card.focusStyle" />

    <TBox
      :x="1"
      :y="2"
      :w="Math.floor((w - 5) / 2)"
      :h="Math.max(7, h - 5)"
      border
      :title="card.stateTitle"
      :style="card.stateBorderStyle"
    >
      <TText
        v-for="(line, lineIndex) in card.stateLines"
        :key="`${card.key}-state-${lineIndex}`"
        :x="0"
        :y="lineIndex"
        :w="Math.floor((w - 11) / 2)"
        :value="line"
        :style="{ fg: 'whiteBright' }"
      />
    </TBox>

    <TBox
      :x="Math.floor((w - 5) / 2) + 2"
      :y="2"
      :w="Math.floor((w - 5) / 2)"
      :h="Math.max(7, h - 5)"
      border
      :title="card.customTitle"
      :style="card.customBorderStyle"
    >
      <TText
        v-for="(line, lineIndex) in card.customLines"
        :key="`${card.key}-custom-${lineIndex}`"
        :x="0"
        :y="lineIndex"
        :w="Math.floor((w - 11) / 2)"
        :value="line"
        :style="line.startsWith('┌') || line.startsWith('└') ? { fg: 'yellowBright' } : { fg: 'whiteBright' }"
      />
    </TBox>
  </TBox>
</template>
