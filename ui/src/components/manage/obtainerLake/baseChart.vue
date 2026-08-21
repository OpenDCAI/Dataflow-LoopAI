<template>
    <div class="ol-chart-shell">
        <canvas ref="canvas"></canvas>
        <div v-if="!hasData" class="ol-chart-empty">{{ emptyText }}</div>
    </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Chart, registerables } from 'chart.js'

Chart.register(...registerables)

const props = defineProps({
    type: {
        type: String,
        default: 'bar'
    },
    chartData: {
        type: Object,
        default: () => ({ labels: [], datasets: [] })
    },
    options: {
        type: Object,
        default: () => ({})
    },
    emptyText: {
        type: String,
        default: 'No Data'
    }
})

const canvas = ref(null)
let chart = null

const hasData = computed(() => {
    return (props.chartData.datasets || []).some((dataset) => {
        return (dataset.data || []).some((value) => Number(value) !== 0)
    })
})

function clone(value) {
    return JSON.parse(JSON.stringify(value || {}))
}

async function renderChart() {
    await nextTick()
    if (!canvas.value) return
    if (chart) {
        chart.destroy()
        chart = null
    }
    chart = new Chart(canvas.value, {
        type: props.type,
        data: clone(props.chartData),
        options: clone(props.options)
    })
}

onMounted(renderChart)
watch(() => props.type, renderChart)
watch(() => props.chartData, renderChart, { deep: true })
watch(() => props.options, renderChart, { deep: true })

onBeforeUnmount(() => {
    if (chart) chart.destroy()
})
</script>

<style lang="scss" scoped>
.ol-chart-shell {
    position: relative;
    width: 100%;
    height: 100%;
    min-height: 0;

    canvas {
        width: 100% !important;
        height: 100% !important;
    }

    .ol-chart-empty {
        @include HcenterVcenter;

        position: absolute;
        inset: 0;
        color: var(--lp-text);
        font-size: 13px;
        pointer-events: none;
    }
}
</style>
