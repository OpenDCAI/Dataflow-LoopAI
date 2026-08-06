<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { TBox, TText } from '@simon_he/vue-tui/vue'

const props = defineProps({
  x: { type: Number, required: true },
  y: { type: Number, required: true },
  w: { type: Number, required: true },
  h: { type: Number, required: true },
  currentTaskName: { type: String, required: true },
  currentTaskId: { type: String, required: true },
  status: { type: String, required: true },
  nodeCount: { type: Number, required: true },
  looperEnabled: { type: Boolean, default: false },
  looperActive: { type: Boolean, default: false },
  looperPending: { type: Boolean, default: false },
  looperRunning: { type: Boolean, default: false },
  looperSeconds: { type: Number, default: 0 },
  local: { type: Function, required: true }
})

const blinkOn = ref(true)
let blinkTimer = null

const looperState = computed(() => {
  if (!props.looperEnabled) return props.local('Disabled')
  if (props.looperRunning) return props.local('Executing')
  if (props.looperPending) return props.local('Waiting for response...')
  if (props.looperActive) return `${props.local('Auto-send in')} ${Math.max(0, props.looperSeconds)}s · ${props.local('Stop with /stop_looper')}`
  return props.local('Ready after next reply')
})

const looperHint = computed(() => {
  if (!props.looperEnabled) return props.local('Idle')
  if (props.looperActive) return props.local('Stop with /stop_looper')
  return props.local('Stop with /stop_looper')
})

const looperStateStyle = computed(() => {
  if (!props.looperEnabled) return { fg: 'white' }
  if (props.looperRunning) return { fg: 'yellowBright', bold: true }
  if (props.looperPending) return { fg: 'cyanBright', bold: true }
  if (props.looperActive) return { fg: 'black', bold: true }
  return { fg: 'green' }
})

const looperBoxStyle = computed(() => {
  if (props.looperActive) {
    return {
      fg: blinkOn.value ? 'yellowBright' : '#f97316',
      bg: blinkOn.value ? 'yellowBright' : '#f97316'
    }
  }
  return { fg: props.looperEnabled ? 'yellowBright' : 'white' }
})

const looperTitleStyle = computed(() => {
  if (props.looperActive) return { fg: 'black', bg: blinkOn.value ? 'yellowBright' : '#f97316', bold: true }
  return { fg: props.looperEnabled ? 'yellowBright' : 'white', bold: true }
})

function stopBlink() {
  if (blinkTimer) {
    clearInterval(blinkTimer)
    blinkTimer = null
  }
  blinkOn.value = true
}

function startBlink() {
  stopBlink()
  blinkTimer = setInterval(() => {
    blinkOn.value = !blinkOn.value
  }, 500)
}

watch(
  () => props.looperActive,
  (active) => {
    if (active) startBlink()
    else stopBlink()
  },
  { immediate: true }
)

onMounted(() => {
  if (props.looperActive) startBlink()
})

onBeforeUnmount(() => {
  stopBlink()
})
</script>

<template>
  <TBox :x="x" :y="y" :w="w" :h="h" border title="Task Summary" :style="{ fg: 'cyanBright' }">
    <TText :x="1" :y="0" :w="Math.max(12, w - 6)" :value="`任务: ${currentTaskName}`" :style="{ fg: 'greenBright', bold: true }" />
    <TText :x="1" :y="1" :w="Math.max(12, w - 6)" :value="`id: ${currentTaskId}   状态: ${status}   节点: ${nodeCount}`" />
    <TBox :x="1" :y="2" :w="Math.max(18, w - 4)" :h="3" border title="Looper" :style="looperBoxStyle" :title-style="looperTitleStyle">
      <TText :x="1" :y="0" :w="Math.max(12, w - 10)" :value="`${local('Status')}: ${looperState}`" :style="looperStateStyle" />
      <TText :x="1" :y="1" :w="Math.max(12, w - 10)" :value="`${local('Task')}: ${looperEnabled ? local('Enabled') : local('Disabled')}   ${looperHint}`" :style="props.looperActive ? { fg: 'black', bold: true } : { fg: 'white' }" />
    </TBox>
  </TBox>
</template>
