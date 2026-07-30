<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { TBox } from '@simon_he/vue-tui/vue'
import { TVirtualMarkdown } from '@simon_he/vue-tui/markdown'

const props = defineProps({
  x: { type: Number, required: true },
  y: { type: Number, required: true },
  w: { type: Number, required: true },
  h: { type: Number, required: true },
  active: { type: Boolean, required: true },
  content: { type: String, required: true },
  scrollTop: { type: Number, required: true },
  bottomSnapToken: { type: Number, required: true }
})

const emit = defineEmits(['update:scrollTop'])
const STICKY_BOTTOM = 1000000
const viewScrollTop = ref(STICKY_BOTTOM)
const stickyToBottom = ref(true)
const lastObservedTop = ref(-1)
let snapTimers = []

function normalizeTop(value) {
  return Math.max(0, Math.floor(Number(value) || 0))
}

function jumpToBottom() {
  stickyToBottom.value = true
  viewScrollTop.value = STICKY_BOTTOM
  lastObservedTop.value = -1
}

function clearSnapTimers() {
  for (const timer of snapTimers) clearTimeout(timer)
  snapTimers = []
}

function scheduleJumpToBottom() {
  clearSnapTimers()
  jumpToBottom()
  void nextTick(() => {
    jumpToBottom()
    for (const delay of [0, 16, 48, 96, 180, 320, 640, 1000]) {
      const timer = setTimeout(() => {
        jumpToBottom()
      }, delay)
      snapTimers.push(timer)
    }
  })
}

function detachTo(top) {
  const next = normalizeTop(top)
  stickyToBottom.value = false
  viewScrollTop.value = next
  lastObservedTop.value = next
  emit('update:scrollTop', next)
}

function handleScroll(top) {
  const next = normalizeTop(top)
  if (stickyToBottom.value) {
    if (lastObservedTop.value >= 0 && next < lastObservedTop.value) {
      detachTo(next)
      return
    }
    lastObservedTop.value = next
    emit('update:scrollTop', next)
    return
  }
  viewScrollTop.value = next
  lastObservedTop.value = next
  emit('update:scrollTop', next)
}

function onKeydown(event) {
  const key = String(event?.key || '')
  const lower = key.toLowerCase()
  const base = stickyToBottom.value ? Math.max(0, lastObservedTop.value) : viewScrollTop.value
  if (lower === 'j') {
    event.preventDefault?.()
    detachTo(base - 1)
    return
  }
  if (lower === 'k') {
    event.preventDefault?.()
    detachTo(base + 1)
    return
  }
  if (key === 'End') {
    event.preventDefault?.()
    jumpToBottom()
  }
}

watch(
  () => props.bottomSnapToken,
  () => {
    scheduleJumpToBottom()
  },
  { immediate: true, flush: 'post' }
)

watch(
  () => props.active,
  (active) => {
    if (!active || !stickyToBottom.value) return
    scheduleJumpToBottom()
  },
  { immediate: true, flush: 'post' }
)

watch(
  () => props.content,
  () => {
    if (!stickyToBottom.value) return
    scheduleJumpToBottom()
  },
  { flush: 'post' }
)

watch(
  () => props.scrollTop,
  (value) => {
    if (stickyToBottom.value) return
    const next = normalizeTop(value)
    viewScrollTop.value = next
    lastObservedTop.value = next
  },
  { immediate: true }
)

onMounted(() => {
  if (stickyToBottom.value) scheduleJumpToBottom()
})

onBeforeUnmount(() => {
  clearSnapTimers()
})
</script>

<template>
  <TBox
    :x="x"
    :y="y"
    :w="w"
    :h="h"
    border
    title="Transcript"
    :style="{ fg: active ? 'greenBright' : 'magentaBright' }"
  >
    <TVirtualMarkdown
      :x="0"
      :y="0"
      :w="Math.max(8, w - 2)"
      :h="Math.max(1, h - 2)"
      :content="content"
      :scrollTop="viewScrollTop"
      :style="{ fg: 'whiteBright' }"
      :auto-focus="active"
      selectable
      @keydown="onKeydown"
      @update:scrollTop="handleScroll"
      @scroll="handleScroll"
    />
  </TBox>
</template>
