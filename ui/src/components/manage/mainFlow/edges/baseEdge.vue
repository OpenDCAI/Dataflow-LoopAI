<template>
    <svg width="100%" height="100%" xmlns="http://www.w3.org/2000/svg" style="overflow: visible">
        <BaseEdge
            :path="path[0]"
            class="lp-flow-edge"
            :style="{ stroke: 'var(--lp-line-hi)', strokeWidth: 1.4 }"
        />
    </svg>
    <!-- Use the `EdgeLabelRenderer` to escape the SVG world of edges and render your own custom label in a `<div>` ctx -->
    <EdgeLabelRenderer>
        <div
            v-show="thisData.label"
            :style="{
                pointerEvents: 'all',
                position: 'absolute',
                transform: `translate(-50%, -50%) translate(${path[1]}px,${path[2]}px)`
            }"
            class="nodrag nopan lp-flow-default-edge-label"
        >
            {{ thisData.label }}
        </div>
    </EdgeLabelRenderer>
</template>

<script setup>
import { BaseEdge, EdgeLabelRenderer, getBezierPath } from '@vue-flow/core'
import { computed } from 'vue'

const props = defineProps({
    sourceX: {
        type: Number,
        required: true
    },
    sourceY: {
        type: Number,
        required: true
    },
    targetX: {
        type: Number,
        required: true
    },
    targetY: {
        type: Number,
        required: true
    },
    sourcePosition: {
        type: String,
        required: true
    },
    targetPosition: {
        type: String,
        required: true
    },
    data: {
        type: Object,
        required: true
    }
})

const path = computed(() => getBezierPath(props))
const thisData = computed(() => ({ label: '', ...props.data }))
</script>

<script>
export default {
    inheritAttrs: false
}
</script>

<style lang="scss">
/* An edge is a hairline; the label is a quiet mono chip, not a gradient pill. */
.lp-flow-default-edge-label {
    height: 18px;
    padding: 0 7px;
    background: var(--lp-chrome);
    border: 1px solid var(--lp-line);
    border-radius: var(--lp-r-full);
    font-family: var(--lp-mono);
    font-size: var(--lp-t-xs);
    color: var(--lp-text-mute);
    display: flex;
    justify-content: center;
    align-items: center;
}
</style>
