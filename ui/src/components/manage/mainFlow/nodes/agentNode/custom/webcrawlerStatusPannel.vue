<template>
    <statusPannel nodePrefix="WebCrawlerAgent" :nodeList="nodeList"></statusPannel>
</template>

<script setup>
import statusPannel from './statusPannel.vue'

const nodeList = [
    {
        value: 'start_node',
        label: '初始化配置'
    },
    {
        value: 'crawl_node',
        label: '网页爬取'
    },
    {
        value: 'webcrawler_dataset_node',
        label: '数据集处理'
    },
    {
        value: 'end_node',
        label: '完成'
    }
]
</script>