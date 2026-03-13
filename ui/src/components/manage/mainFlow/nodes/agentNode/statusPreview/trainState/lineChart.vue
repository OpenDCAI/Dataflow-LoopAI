<template>
    <canvas ref="canvas" width="600" height="400"></canvas>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'
import {
    Chart,
    LineController,
    LineElement,
    PointElement,
    CategoryScale,
    LinearScale,
    Title,
    Tooltip,
    Legend
} from 'chart.js'

// 注册 Chart.js 模块
Chart.register(
    LineController,
    LineElement,
    PointElement,
    CategoryScale,
    LinearScale,
    Title,
    Tooltip,
    Legend
)

const props = defineProps({
    title: {
        type: String,
        default: 'Chart.js 实时折线图'
    },
    labels: {
        type: Array,
        default: () => ['1', '2', '3', '4', '5']
    },
    data: {
        type: Array,
        default: () => [10, 20, 30, 40, 50]
    },
    dataLabel: {
        type: String,
        default: 'Data'
    }
})

const canvas = ref(null)
let myChart = null

onMounted(() => {
    // 初始化图表
    myChart = new Chart(canvas.value, {
        type: 'line',
        data: {
            labels: props.labels,
            datasets: [
                {
                    label: props.dataLabel,
                    data: props.data,
                    borderColor: 'rgba(126, 113, 208, 1)',
                    backgroundColor: 'rgba(126, 113, 208, 0.2)',
                    tension: 0.4
                }
            ]
        },
        options: {
            responsive: true,
            plugins: {
                legend: { display: true },
                title: { display: true, text: props.title }
            },
            scales: {
                y: { beginAtZero: true }
            }
        }
    })

    const updateData = () => {
        myChart.data.labels = props.labels
        myChart.data.datasets[0].data = props.data
        myChart.update()
        myChart.options.plugins.title.text = props.title
        myChart.data.datasets[0].label = props.dataLabel
    }
    watch(() => props.data, updateData, { deep: true })
    watch(() => props.labels, updateData, { deep: true })
})
</script>
