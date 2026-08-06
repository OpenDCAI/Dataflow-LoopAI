<script setup>
import { TInputBox, TText } from '@simon_he/vue-tui/vue'

const props = defineProps({
  x: { type: Number, required: true },
  y: { type: Number, required: true },
  w: { type: Number, required: true },
  inputBoxHeight: { type: Number, default: 3 },
  modelValue: { type: String, required: true },
  placeholder: { type: String, required: true },
  commandHint: { type: String, required: true },
  statusLine: { type: String, required: true },
  helpLines: { type: Array, default: () => [] }
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
    :placeholder="props.placeholder"
    :style="{ fg: 'whiteBright' }"
    auto-focus
    @update:model-value="(value) => emit('update:modelValue', value)"
    @keydown="(event) => emit('keydown', event)"
  />
  <template v-if="helpLines.length">
    <TText
      v-for="(line, index) in helpLines"
      :key="`command-help-${index}`"
      :x="2"
      :y="y + 2 + index"
      :w="Math.max(12, w - 6)"
      :value="line"
      :style="index === 0 ? { fg: 'yellowBright', bold: true } : { fg: 'whiteBright' }"
    />
  </template>
  <template v-else>
    <TText :x="2" :y="y + 2" :w="Math.max(12, w - 6)" :value="commandHint" :style="{ fg: 'white' }" />
    <TText :x="2" :y="y + 3" :w="Math.max(12, w - 6)" :value="statusLine" :style="{ fg: 'greenBright' }" />
  </template>
</template>
