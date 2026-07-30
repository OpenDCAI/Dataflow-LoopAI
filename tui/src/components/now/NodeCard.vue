<script setup>
import { TBox, TText } from '@simon_he/vue-tui/vue'

defineProps({
  card: { type: Object, required: true },
  index: { type: Number, required: true },
  x: { type: Number, required: true },
  y: { type: Number, required: true },
  w: { type: Number, required: true },
  h: { type: Number, required: true }
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
  >
    <TText :x="1" :y="0" :w="w - 4" :value="`updated: ${card.runtimeUpdatedLabel || '-'}`" :style="{ fg: 'white' }" />
    <TText :x="1" :y="1" :w="w - 4" :value="card.focusLabel" :style="card.focusStyle" />

    <TBox
      :x="1"
      :y="3"
      :w="Math.floor((w - 5) / 2)"
      :h="Math.max(6, h - 7)"
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
      :y="3"
      :w="Math.floor((w - 5) / 2)"
      :h="Math.max(6, h - 7)"
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
