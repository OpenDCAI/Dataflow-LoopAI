<template>
    <svg width="100%" height="100%" xmlns="http://www.w3.org/2000/svg" style="overflow: visible">
        <g>
            <path
                class="vue-flow__connection"
                fill="none"
                stroke="var(--lp-accent)"
                :stroke-width="1.4"
                :d="getBezierPath(props)[0]"
            />
            <circle
                :cx="targetX"
                :cy="targetY"
                fill="var(--lp-bg)"
                :r="3.5"
                stroke="var(--lp-accent)"
                :stroke-width="1.4"
            />
        </g>
    </svg>
</template>

<script setup>
import { getBezierPath } from '@vue-flow/core'

const props = defineProps({
    sourceX: {
        type: Number,
        required: true
    },
    sourceY: {
        type: Number,
        required: true
    },
    sourcePosition: {
        type: String,
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
    targetPosition: {
        type: String,
        required: true
    },
    data: {
        type: Object
    }
})
</script>
