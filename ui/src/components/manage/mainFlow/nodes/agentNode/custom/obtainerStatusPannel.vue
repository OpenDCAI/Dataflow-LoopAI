<template>
    <div class="obtainer-status-panel">
        <div class="status-grid">
            <div v-for="item in capabilityCards" :key="item.key" class="status-card">
                <span class="status-dot" :class="item.level"></span>
                <div class="status-body">
                    <span class="status-name">{{ item.label }}</span>
                    <span class="status-value" :title="item.detail">{{ item.value }}</span>
                </div>
            </div>
        </div>

        <div v-if="embeddingProgress.visible" class="section">
            <div class="section-title">
                <span>Embedding</span>
                <span>{{ embeddingProgress.percentText }}</span>
            </div>
            <fv-progress-bar
                :model-value="embeddingProgress.percent"
                :foreground="foreground"
                background="rgba(200, 200, 200, 0.28)"
                :border-radius="4"
                style="width: 100%; height: 5px"
            ></fv-progress-bar>
            <div class="muted-line">
                {{ embeddingProgress.detail }}
            </div>
        </div>

        <div class="section">
            <div class="section-title">
                <span>当前任务</span>
                <span>{{ activeTaskItems.length }}</span>
            </div>
            <div class="task-list">
                <div v-for="task in activeTaskItems" :key="task.key" class="task-row">
                    <span class="task-chip" :class="task.level">{{ task.label }}</span>
                    <span class="task-text" :title="task.detail">{{ task.detail }}</span>
                </div>
                <div v-if="activeTaskItems.length === 0" class="empty-line">空闲</div>
            </div>
        </div>

        <div class="section">
            <div class="section-title">
                <span>Stream</span>
                <span :class="['stream-state', streamLevel]">{{ streamStatusText }}</span>
            </div>
            <div class="stream-list">
                <div v-for="item in streamItems" :key="item.key" class="stream-row">
                    <span class="stream-source">{{ item.source }}</span>
                    <span class="stream-message" :title="item.message">{{ item.message }}</span>
                </div>
                <div v-if="streamItems.length === 0" class="empty-line">暂无流反馈</div>
            </div>
        </div>
    </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useLoopAI } from '@/stores/loopAI'
import {
    getDataMixerCommands,
    getDataMixerEmbeddingHealth,
    getDataMixerMonitor
} from '@/services/obtainerLake'

const props = defineProps({
    foreground: {
        type: String,
        default: 'rgba(90, 45, 133, 1)'
    }
})

const loopAI = useLoopAI()
const lakePath = '.loopai/lake.yaml'
const monitor = ref(null)
const embeddingHealth = ref(null)
const commandGroups = ref([])
const lastError = ref('')
let refreshTimer = null

const normalizeText = (value) => String(value || '').trim()
const toLower = (value) => normalizeText(value).toLowerCase()
const isTrueValue = (value) => ['1', 'true', 'yes', 'on'].includes(toLower(value))

const formatNumber = (value) => {
    const num = Number(value || 0)
    if (!Number.isFinite(num)) return '0'
    if (num >= 1000000) return `${(num / 1000000).toFixed(1)}M`
    if (num >= 1000) return `${(num / 1000).toFixed(1)}K`
    return String(num)
}

const formatPercent = (value) => {
    const num = Number(value || 0)
    if (!Number.isFinite(num)) return '0%'
    return `${Math.round(num * 100)}%`
}

const shortText = (value, limit = 80) => {
    const text = normalizeText(value)
    if (text.length <= limit) return text
    return `${text.slice(0, limit - 1)}...`
}

const allCommandText = computed(() => {
    return commandGroups.value
        .flatMap((group) => group.commands || [])
        .join('\n')
        .toLowerCase()
})

const lakeInitialized = computed(() => {
    return Boolean(monitor.value?.config?.warehouse || monitor.value?.lake_root)
})

const summary = computed(() => monitor.value?.summary || {})
const embedding = computed(() => monitor.value?.embedding || {})

const cacheLevel = computed(() => {
    if (!lakeInitialized.value) return 'bad'
    if (monitor.value?.cache_status === 'stale' || monitor.value?.stale) return 'warn'
    return 'good'
})

const embeddingAvailable = computed(() => {
    const healthStatus = toLower(embeddingHealth.value?.status || embedding.value?.probe?.status)
    if (['ok', 'success', 'healthy', 'online'].includes(healthStatus)) return true
    if (healthStatus === 'offline') return false
    return Boolean(embedding.value?.embedding_model || embedding.value?.embedding_backend)
})

const embeddingLevel = computed(() => {
    const healthStatus = toLower(embeddingHealth.value?.status || embedding.value?.probe?.status)
    if (['offline', 'error', 'failed'].includes(healthStatus)) return 'bad'
    if (Number(embedding.value?.pending_records || 0) > 0) return 'warn'
    return embeddingAvailable.value ? 'good' : 'bad'
})

const coworkerAvailable = computed(() => {
    const text = allCommandText.value
    return text.includes('dataset-acquisition-agent') && text.includes('sft-export-agent')
})

const qualityAvailable = computed(() => {
    const text = allCommandText.value
    return text.includes('quality_score') || text.includes('dataflow agent-run') || text.includes('op run')
})

const capabilityCards = computed(() => {
    const records = Number(summary.value.records || embedding.value.total_records || 0)
    const warehouse = monitor.value?.config?.warehouse || monitor.value?.lake_root || '未加载'
    const healthStatus = embeddingHealth.value?.status || embedding.value?.probe?.status || 'not_checked'
    const qualityFindings = Number(summary.value.quality_findings || 0)

    return [
        {
            key: 'lake',
            label: '数据湖',
            value: lakeInitialized.value ? `${formatNumber(records)} records` : '未初始化',
            detail: lakeInitialized.value ? warehouse : lastError.value || '未找到当前 lake pointer',
            level: cacheLevel.value
        },
        {
            key: 'embedding',
            label: 'Embedding',
            value: embeddingAvailable.value ? `${formatPercent(embedding.value.coverage)} indexed` : '不可用',
            detail: `${embedding.value.embedding_model || '-'} / ${healthStatus}`,
            level: embeddingLevel.value
        },
        {
            key: 'coworker',
            label: 'Coworker',
            value: coworkerAvailable.value ? '可用' : '未就绪',
            detail: coworkerAvailable.value
                ? 'dataset-acquisition-agent / sft-export-agent'
                : '命令面未暴露 worker',
            level: coworkerAvailable.value ? 'good' : 'warn'
        },
        {
            key: 'quality',
            label: '质量校验',
            value: qualityAvailable.value ? '可用' : '未就绪',
            detail: qualityFindings ? `${qualityFindings} findings` : 'DataMixer quality/dataflow',
            level: qualityAvailable.value ? 'good' : 'warn'
        }
    ]
})

const embeddingProgress = computed(() => {
    const total = Number(embedding.value.total_records || 0)
    const indexed = Number(embedding.value.indexed_records || 0)
    const pending = Number(embedding.value.pending_records || 0)
    const coverage = Number(embedding.value.coverage || 0)
    return {
        visible: Boolean(total || indexed || pending),
        percent: Math.max(0, Math.min(100, coverage * 100)),
        percentText: formatPercent(coverage),
        detail: `${formatNumber(indexed)} indexed / ${formatNumber(pending)} pending`
    }
})

const taskStatus = computed(() => loopAI.taskStatus || {})
const customInfoEntries = computed(() => {
    const info = taskStatus.value.custom_info || {}
    return Object.entries(info).map(([key, value]) => ({
        key,
        value: value || {},
        lowerKey: key.toLowerCase()
    }))
})

const runningTasks = computed(() => {
    const tasks = taskStatus.value.running_tasks || []
    return Array.isArray(tasks) ? tasks.map((task) => normalizeText(task)).filter(Boolean) : []
})

const matchRunningText = (patterns) => {
    return runningTasks.value.find((task) => {
        const lowered = task.toLowerCase()
        return patterns.some((pattern) => lowered.includes(pattern))
    })
}

const matchCustomInfo = (patterns) => {
    return customInfoEntries.value.find((entry) => {
        const message = toLower(entry.value?.message)
        const current = toLower(entry.value?.current)
        const data = toLower(JSON.stringify(entry.value?.data || {}))
        return patterns.some(
            (pattern) =>
                entry.lowerKey.includes(pattern) ||
                message.includes(pattern) ||
                current.includes(pattern) ||
                data.includes(pattern)
        )
    })
}

const buildTaskItem = ({ key, label, patterns, fallbackDetail, activeFallback = false }) => {
    const running = matchRunningText(patterns)
    const event = matchCustomInfo(patterns)
    const progress = Number(event?.value?.progress)
    const detail =
        event?.value?.message ||
        running ||
        (activeFallback ? fallbackDetail : '')
    const active =
        Boolean(running || event) ||
        activeFallback
    if (!active) return null
    return {
        key,
        label,
        detail: progress > 0 && progress < 1 ? `${Math.round(progress * 100)}% ${detail}` : detail,
        level: event?.value?.data?.error ? 'bad' : 'running'
    }
}

const activeTaskItems = computed(() => {
    const pendingEmbeddings = Number(embedding.value.pending_records || 0)
    const autoEmbed = isTrueValue(embedding.value.auto_embed)
    return [
        buildTaskItem({
            key: 'ingest-agent',
            label: '入湖',
            patterns: ['dataset-acquisition-agent', 'acquisition', 'download', 'ingest', 'agent-ingest'],
            fallbackDetail: ''
        }),
        buildTaskItem({
            key: 'export-agent',
            label: '出湖',
            patterns: ['sft-export-agent', 'recipe', 'export', 'snapshot'],
            fallbackDetail: ''
        }),
        buildTaskItem({
            key: 'embedding',
            label: 'Embedding',
            patterns: ['embedding', 'index build', 'recall'],
            fallbackDetail: `${formatNumber(pendingEmbeddings)} records pending`,
            activeFallback: autoEmbed && pendingEmbeddings > 0 && embeddingLevel.value !== 'bad'
        })
    ].filter(Boolean)
})

const streamStatusText = computed(() => {
    if (loopAI.msgStreamModel?.loading) return 'running'
    if (taskStatus.value.stream_message) return 'active'
    if (taskStatus.value.event_streaming && taskStatus.value.event_streaming !== 'not_ready') {
        return taskStatus.value.event_streaming
    }
    return 'idle'
})

const streamLevel = computed(() => {
    return ['running', 'active'].includes(streamStatusText.value) ? 'running' : 'idle'
})

const codexStreamItems = computed(() => {
    const msgs = loopAI.msgStreamModel?.msgs || []
    return msgs
        .slice(-8)
        .map((msg, index) => {
            const content =
                msg?.data?.content ||
                msg?.data?.text ||
                msg?.content ||
                msg?.message ||
                ''
            return {
                key: `codex-${msgs.length - index}`,
                source: msg?.type || 'codex',
                message: shortText(content)
            }
        })
        .filter((item) => item.message)
})

const eventStreamItems = computed(() => {
    return customInfoEntries.value
        .filter((entry) => entry.lowerKey.includes('obtainer'))
        .slice(-6)
        .map((entry) => ({
            key: entry.key,
            source: entry.key.split('.').slice(-1)[0] || 'obtainer',
            message: shortText(entry.value?.message || entry.value?.current || entry.key)
        }))
})

const streamItems = computed(() => {
    const items = []
    if (taskStatus.value.stream_message) {
        items.push({
            key: 'starter-stream',
            source: 'starter',
            message: shortText(taskStatus.value.stream_message, 110)
        })
    }
    items.push(...eventStreamItems.value, ...codexStreamItems.value)
    return items.slice(-6).reverse()
})

const loadMonitor = async ({ probe = false } = {}) => {
    try {
        const res = await getDataMixerMonitor({ lake: lakePath })
        monitor.value = res?.data || null
        lastError.value = res?.data?.error || ''
    } catch (error) {
        lastError.value = error?.message || 'lake monitor failed'
    }

    if (probe) {
        try {
            const res = await getDataMixerEmbeddingHealth({
                lake: lakePath,
                timeout_seconds: 2
            })
            embeddingHealth.value = res?.data || null
        } catch (error) {
            embeddingHealth.value = {
                status: 'offline',
                message: error?.message || 'embedding probe failed'
            }
        }
    }
}

const loadCommands = async () => {
    try {
        const res = await getDataMixerCommands()
        commandGroups.value = res?.data?.groups || []
    } catch (error) {
        commandGroups.value = []
    }
}

onMounted(async () => {
    await Promise.all([loadCommands(), loadMonitor({ probe: true })])
    refreshTimer = window.setInterval(() => {
        loadMonitor({ probe: true })
    }, 12000)
})

onBeforeUnmount(() => {
    if (refreshTimer) {
        window.clearInterval(refreshTimer)
        refreshTimer = null
    }
})
</script>

<style lang="scss" scoped>
.obtainer-status-panel {
    width: 100%;
    padding: 8px 6px 10px;
    box-sizing: border-box;
}

.status-grid {
    display: grid;
    grid-template-columns: 1fr;
    gap: 6px;
}

.status-card {
    display: flex;
    align-items: center;
    gap: 8px;
    min-height: 36px;
    padding: 7px 8px;
    border-radius: 6px;
    background: rgba(255, 255, 255, 0.64);
    border: 1px solid rgba(90, 45, 133, 0.12);
    box-sizing: border-box;
}

.status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;

    &.good {
        background: #21a366;
    }

    &.warn,
    &.running {
        background: #d99000;
    }

    &.bad {
        background: #d13438;
    }
}

.status-body {
    min-width: 0;
    flex: 1;
    display: grid;
    grid-template-columns: 70px minmax(0, 1fr);
    align-items: center;
    gap: 6px;
}

.status-name {
    font-size: 12px;
    font-weight: 600;
    color: rgba(45, 45, 45, 0.9);
    white-space: nowrap;
}

.status-value {
    min-width: 0;
    font-size: 12px;
    color: rgba(35, 35, 35, 0.86);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    text-align: right;
}

.section {
    margin-top: 10px;
    padding-top: 8px;
    border-top: 1px solid rgba(90, 45, 133, 0.12);
}

.section-title {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
    font-size: 12px;
    font-weight: 700;
    color: rgba(45, 45, 45, 0.88);
}

.muted-line,
.empty-line {
    margin-top: 5px;
    font-size: 11px;
    color: rgba(75, 75, 75, 0.62);
}

.task-list,
.stream-list {
    display: flex;
    flex-direction: column;
    gap: 5px;
}

.task-row {
    display: grid;
    grid-template-columns: 58px minmax(0, 1fr);
    gap: 6px;
    align-items: center;
}

.task-chip {
    display: inline-flex;
    justify-content: center;
    align-items: center;
    height: 20px;
    padding: 0 6px;
    border-radius: 5px;
    font-size: 11px;
    font-weight: 700;
    color: white;
    background: #767676;

    &.running {
        background: #8a5a00;
    }

    &.bad {
        background: #a4262c;
    }
}

.task-text,
.stream-message {
    min-width: 0;
    font-size: 11px;
    color: rgba(45, 45, 45, 0.8);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.stream-state {
    font-size: 11px;
    font-weight: 700;

    &.running {
        color: #8a5a00;
    }

    &.idle {
        color: rgba(75, 75, 75, 0.62);
    }
}

.stream-row {
    display: grid;
    grid-template-columns: 64px minmax(0, 1fr);
    gap: 6px;
    align-items: center;
}

.stream-source {
    min-width: 0;
    font-size: 10px;
    font-weight: 700;
    color: rgba(90, 45, 133, 0.8);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
</style>
