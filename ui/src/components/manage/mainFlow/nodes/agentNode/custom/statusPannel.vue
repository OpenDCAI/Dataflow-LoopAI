v<template>
    <div class="status-log-panel">
        <div class="log-header">
            <fv-progress-ring v-if="isRunning" :loading="true" :r="8" :border-width="2"
                background="rgba(200, 200, 200, 0.3)" :color="foreground"></fv-progress-ring>
            <i v-else class="ms-Icon ms-Icon--Processing" :style="{ color: foreground }"></i>
            <span class="header-title" :style="{ color: foreground }">{{ appConfig.local('Node Progress') }}</span>
        </div>
        <div class="log-list">
            <div v-for="(node, index) in orderedNodes" :key="node.key" class="log-item">
                <div class="node-indicator">
                    <div class="node-dot" :class="getNodeStatus(node)">
                        <i v-if="getNodeStatus(node) === NodeStatus.COMPLETED" class="ms-Icon ms-Icon--CheckMark"></i>
                        <i v-else-if="getNodeStatus(node) === NodeStatus.FAILED" class="ms-Icon ms-Icon--Cancel"></i>
                        <fv-progress-ring v-else-if="getNodeStatus(node) === NodeStatus.RUNNING" :loading="true" :r="6"
                            :border-width="2" background="transparent" color="white"></fv-progress-ring>
                        <span v-else>{{ index + 1 }}</span>
                    </div>
                    <div v-if="index < orderedNodes.length - 1" class="node-line"
                        :class="{ 'completed': getNodeStatus(node) === NodeStatus.COMPLETED }"></div>
                </div>
                <div class="node-content">
                    <div class="node-header">
                        <span class="node-name">{{ getNodeDisplayName(node.nodeName) }}</span>
                        <span class="node-status-tag">
                            {{ NodeStatusMap[getNodeStatus(node)].name }}
                        </span>
                    </div>
                    <p v-if="node.value && node.value.message" class="node-message" :title="node.value.message">
                        {{ node.value.message }}
                    </p>
                    <div v-if="node.value && node.value.progress" class="progress-wrapper">
                        <fv-progress-bar :model-value="node.value.progress * 100" :foreground="foreground"
                            background="rgba(200, 200, 200, 0.3)" :border-radius="4"
                            style="width: 100%; height: 4px"></fv-progress-bar>
                        <span class="progress-text">{{ Math.round(node.value.progress * 100) }}%</span>
                    </div>
                </div>
            </div>
        </div>
    </div>
</template>

<script setup>
import { computed } from 'vue'
import { useAppConfig } from '@/stores/appConfig'
import { useLoopAI } from '@/stores/loopAI'

const appConfig = useAppConfig()
const loopAI = useLoopAI()

const props = defineProps({
    nodePrefix: {
        type: String,
        default: 'ObtainerAgent'
    },
    nodeList: {
        type: Array,
        default: () => [{
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
        }]
    },
    foreground: {
        type: String,
        default: 'rgba(134, 127, 163, 1)'
    },
})


const NodeStatus = {
    PENDING: 'pending',
    RUNNING: 'running',
    COMPLETED: 'completed',
    FAILED: 'failed'
}

const NodeStatusMap = {
    [NodeStatus.PENDING]: {
        name: '未开始',
        color: 'rgba(200, 200, 200, 0.3)'
    },
    [NodeStatus.RUNNING]: {
        name: '进行中',
        color: 'rgba(200, 200, 200, 0.3)'
    },
    [NodeStatus.COMPLETED]: {
        name: '已完成',
        color: 'rgba(200, 200, 200, 0.3)'
    },
    [NodeStatus.FAILED]: {
        name: '失败',
        color: 'rgba(200, 200, 200, 0.3)'
    }
}

const customInfo = computed(() => {
    return loopAI.taskStatus.custom_info
})

// 按顺序整理节点信息
const orderedNodes = computed(() => {
    const info = customInfo.value
    if (!info) {
        return props.nodeList.map(node => ({
            key: `${props.nodePrefix}.${node.value}`,
            nodeName: node.value,
            value: null
        }))
    }

    return props.nodeList.map(node => {
        const key = `${props.nodePrefix}.${node.value}`
        return {
            key: key,
            nodeName: node.value,
            value: info[key] || null
        }
    })
})

const getNodeDisplayName = (nodeName) => {
    return props.nodeList.find(node => node.value === nodeName)?.label || nodeName
}

const getNodeStatus = (node) => {
    const { progress } = node.value || {}
    const { error } = node.value?.data || {}
    if (error) return NodeStatus.FAILED
    if (progress === undefined) return NodeStatus.PENDING
    if (progress >= 1) return NodeStatus.COMPLETED
    if (progress >= 0) return NodeStatus.RUNNING
    return NodeStatus.PENDING
}

// 判断整体是否在运行
const isRunning = computed(() => {
    return orderedNodes.value.some(node => getNodeStatus(node) === NodeStatus.RUNNING)
})


</script>

<style lang="scss" scoped>
.status-log-panel {
    width: 100%;
    padding: 8px 0;

    .log-header {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 0 8px 8px;
        border-bottom: 1px solid rgba(200, 200, 200, 0.3);
        margin-bottom: 8px;

        .header-title {
            font-size: 13px;
            font-weight: 600;
        }
    }

    .log-list {
        display: flex;
        flex-direction: column;
        gap: 0;
    }

    .log-item {
        display: flex;
        gap: 10px;
        padding: 6px 8px;
        border-radius: 6px;
        transition: background-color 0.2s;

        &:hover {
            background-color: rgba(200, 200, 200, 0.1);
        }

        &.running {
            background-color: rgba(255, 165, 0, 0.08);
        }

        &.completed {
            opacity: 0.85;
        }

        &.failed {
            background-color: rgba(220, 38, 45, 0.08);
        }
    }

    .node-indicator {
        display: flex;
        flex-direction: column;
        align-items: center;
        min-width: 20px;

        .node-dot {
            width: 20px;
            height: 20px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 10px;
            font-weight: 600;
            color: white;
            background-color: rgba(200, 200, 200, 0.5);
            transition: all 0.3s;
            flex-shrink: 0;

            &.pending {
                background-color: rgba(200, 200, 200, 0.5);
                color: rgba(100, 100, 100, 1);
            }

            &.running {
                background-color: rgba(255, 165, 0, 1);
                color: white;
            }

            &.completed {
                background-color: rgba(45, 168, 83, 1);
                color: white;
            }

            &.failed {
                background-color: rgba(220, 38, 45, 1);
                color: white;
            }
        }

        .node-line {
            width: 2px;
            flex: 1;
            min-height: 16px;
            background-color: rgba(200, 200, 200, 0.5);
            transition: background-color 0.3s;

            &.completed {
                background-color: rgba(45, 168, 83, 1);
            }
        }
    }

    .node-content {
        flex: 1;
        min-width: 0;

        .node-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            margin-bottom: 2px;
        }

        .node-name {
            font-size: 12px;
            font-weight: 600;
            color: rgba(50, 50, 50, 1);
        }

        .node-status-tag {
            font-size: 10px;
            padding: 1px 6px;
            border-radius: 4px;
            flex-shrink: 0;

            &.pending {
                background-color: rgba(200, 200, 200, 0.3);
                color: rgba(100, 100, 100, 1);
            }

            &.running {
                background-color: rgba(255, 165, 0, 0.2);
                color: rgba(200, 120, 0, 1);
            }

            &.completed {
                background-color: rgba(45, 168, 83, 0.2);
                color: rgba(30, 120, 60, 1);
            }

            &.failed {
                background-color: rgba(220, 38, 45, 0.2);
                color: rgba(180, 30, 35, 1);
            }
        }

        .node-message {
            font-size: 11px;
            color: rgba(100, 100, 100, 1);
            margin: 0;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            line-height: 1.4;
        }

        .progress-wrapper {
            display: flex;
            align-items: center;
            gap: 6px;
            margin-top: 4px;

            .progress-text {
                font-size: 10px;
                color: rgba(100, 100, 100, 1);
                min-width: 30px;
            }
        }
    }
}
</style>
