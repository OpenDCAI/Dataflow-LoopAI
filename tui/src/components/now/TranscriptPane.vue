<script setup>
import AssistantPane from './AssistantPane.vue'
import RuntimePane from './RuntimePane.vue'
import UserPane from './UserPane.vue'

const props = defineProps({
  x: { type: Number, required: true },
  y: { type: Number, required: true },
  w: { type: Number, required: true },
  h: { type: Number, required: true },
  nowActivePane: { type: String, required: true },
  userMarkdown: { type: String, required: true },
  runtimeMarkdown: { type: String, required: true },
  transcriptMarkdown: { type: String, required: true },
  toolScrollTop: { type: Number, required: true },
  assistantScrollTop: { type: Number, required: true },
  runtimeBottomSnapToken: { type: Number, required: true },
  assistantBottomSnapToken: { type: Number, required: true }
})

const emit = defineEmits(['update:toolScrollTop', 'update:assistantScrollTop'])
</script>

<template>
  <UserPane :x="x" :y="y" :w="Math.floor(w * 0.3)" :h="h" :content="userMarkdown" />
  <RuntimePane
    :x="x + Math.floor(w * 0.3)"
    :y="y"
    :w="Math.floor(w * 0.3)"
    :h="h"
    :active="nowActivePane === 'tool'"
    :content="runtimeMarkdown"
    :scroll-top="toolScrollTop"
    :bottom-snap-token="runtimeBottomSnapToken"
    @update:scroll-top="(value) => emit('update:toolScrollTop', value)"
  />
  <AssistantPane
    :x="x + Math.floor(w * 0.6)"
    :y="y"
    :w="w - Math.floor(w * 0.6)"
    :h="h"
    :active="nowActivePane === 'assistant'"
    :content="transcriptMarkdown"
    :scroll-top="assistantScrollTop"
    :bottom-snap-token="assistantBottomSnapToken"
    @update:scroll-top="(value) => emit('update:assistantScrollTop', value)"
  />
</template>
