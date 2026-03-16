<template>
    <StatusPannel nodePrefix="ObtainerAgent" :nodeList="nodeList"></StatusPannel>
</template>

<script setup>
import StatusPannel from './statusPannel.vue'

const nodeList = [
    {
        value: 'start_node',
        label: '初始化配置'
    }, {
        value: 'task_decomposer_node',
        label: '任务分解'
    }, {
        value: 'web_search_node',
        label: '网页搜索'
    }, {
        value: 'download_node',
        label: '下载'
    }, {
        value: 'end_node',
        label: '完成'
    }
]


</script>