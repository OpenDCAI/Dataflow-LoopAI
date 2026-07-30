<script setup>
import { TBox, TText } from '@simon_he/vue-tui/vue'

defineProps({
  x: { type: Number, required: true },
  y: { type: Number, required: true },
  w: { type: Number, required: true },
  h: { type: Number, required: true },
  title: { type: String, required: true },
  visibleNodeCards: { type: Array, required: true },
  nowActivePane: { type: String, required: true },
  emptyText: { type: String, required: true },
  nodeCardWidth: { type: Number, default: 44 },
  nodeCardGap: { type: Number, default: 2 }
})
</script>

<template>
  <TBox :x="x" :y="y" :w="w" :h="h" border :title="title" :style="{ fg: 'yellowBright' }">
    <template v-if="visibleNodeCards.length">
      <template v-for="(card, index) in visibleNodeCards" :key="`${card.key}-${index}`">
        <TBox
          :x="index * (nodeCardWidth + nodeCardGap)"
          :y="0"
          :w="nodeCardWidth"
          :h="Math.max(8, h - 2)"
          border
          :title="`${card.label} · ${card.runtimeStatus}`"
          :style="card.borderStyle"
        >
          <TText :x="1" :y="0" :w="nodeCardWidth - 4" :value="`updated: ${card.runtimeUpdatedLabel || '-'}`" :style="{ fg: 'white' }" />
          <TText :x="1" :y="1" :w="nodeCardWidth - 4" :value="card.focusLabel" :style="card.focusStyle" />

          <TBox
            :x="1"
            :y="3"
            :w="Math.floor((nodeCardWidth - 5) / 2)"
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
              :w="Math.floor((nodeCardWidth - 11) / 2)"
              :value="line"
              :style="{ fg: 'whiteBright' }"
            />
          </TBox>

          <TBox
            :x="Math.floor((nodeCardWidth - 5) / 2) + 2"
            :y="3"
            :w="Math.floor((nodeCardWidth - 5) / 2)"
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
              :w="Math.floor((nodeCardWidth - 11) / 2)"
              :value="line"
              :style="line.startsWith('┌') || line.startsWith('└') ? { fg: 'yellowBright' } : { fg: 'whiteBright' }"
            />
          </TBox>
        </TBox>
      </template>
    </template>
    <TText v-else :x="1" :y="1" :w="Math.max(12, w - 6)" :value="emptyText" :style="{ fg: 'redBright' }" />
  </TBox>
</template>
