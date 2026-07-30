<script setup>
import { TInputBox, TText } from '@simon_he/vue-tui/vue'

const props = defineProps({
  x: { type: Number, required: true },
  y: { type: Number, required: true },
  w: { type: Number, required: true },
  inputBoxHeight: { type: Number, default: 3 },
  modelValue: { type: String, required: true },
  commandHint: { type: String, required: true },
  statusLine: { type: String, required: true }
})

const emit = defineEmits(['update:modelValue', 'keydown'])
</script>

<template>
  <TInputBox
    :x="x"
    :y="y"
    :w="w"
    :h="inputBoxHeight"
    title="Command"
    :model-value="props.modelValue"
    placeholder="/tasks, /now, /new demo, 或直接发送消息"
    :style="{ fg: 'whiteBright' }"
    auto-focus
    @update:model-value="(value) => emit('update:modelValue', value)"
    @keydown="(event) => emit('keydown', event)"
  />
  <TText :x="2" :y="y + 2" :w="Math.max(12, w - 6)" :value="commandHint" :style="{ fg: 'white' }" />
  <TText :x="2" :y="y + 3" :w="Math.max(12, w - 6)" :value="statusLine" :style="{ fg: 'greenBright' }" />
</template>
