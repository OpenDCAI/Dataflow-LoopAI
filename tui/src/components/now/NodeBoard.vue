<script setup>
import { TBox, TText } from '@simon_he/vue-tui/vue'
import NodeCard from './NodeCard.vue'

defineProps({
  x: { type: Number, required: true },
  y: { type: Number, required: true },
  w: { type: Number, required: true },
  h: { type: Number, required: true },
  title: { type: String, required: true },
  visibleNodeCards: { type: Array, required: true },
  emptyText: { type: String, required: true },
  nodeCardWidth: { type: Number, default: 44 },
  nodeCardGap: { type: Number, default: 2 }
})
</script>

<template>
  <TBox :x="x" :y="y" :w="w" :h="h" border :title="title" :style="{ fg: 'yellowBright' }">
    <template v-if="visibleNodeCards.length">
      <NodeCard
        v-for="(card, index) in visibleNodeCards"
        :key="`${card.key}-${index}`"
        :card="card"
        :index="index"
        :x="index * (nodeCardWidth + nodeCardGap)"
        :y="0"
        :w="nodeCardWidth"
        :h="Math.max(8, h - 2)"
      />
    </template>
    <TText v-else :x="1" :y="1" :w="Math.max(12, w - 6)" :value="emptyText" :style="{ fg: 'redBright' }" />
  </TBox>
</template>
