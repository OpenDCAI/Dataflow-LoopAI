<template>
    <div class="obtainer-status-panel" :class="{ active: operationalActive }">
        <section class="status-column lake-column">
            <header class="column-header">
                <div>
                    <span class="column-kicker">01</span>
                    <h3>数据湖状态</h3>
                </div>
                <span :class="['state-label', initialized ? 'good' : 'idle']">
                    {{ initialized ? '已初始化' : '未初始化' }}
                </span>
            </header>

            <div class="metric-grid">
                <div v-for="item in lakeMetrics" :key="item.label" class="metric-item">
                    <span>{{ item.label }}</span>
                    <strong>{{ exactNumber(item.value) }}</strong>
                </div>
            </div>

            <div class="subsection">
                <div class="subsection-title"><span>分层数据</span><span>记录数</span></div>
                <div v-for="layer in layerRows" :key="layer.level" class="level-row">
                    <span class="level-name">{{ layer.level }}</span>
                    <span class="level-detail" :title="(layer.datasets || []).join(', ')">
                        {{ layer.label }}
                    </span>
                    <strong>{{ exactNumber(layer.count) }}</strong>
                </div>
            </div>

            <div class="subsection compact-metrics">
                <div class="inline-metric"><span>Embedding 已索引</span><strong>{{ exactNumber(embedding.indexed_records) }}</strong></div>
                <div class="inline-metric"><span>Embedding 待处理</span><strong>{{ exactNumber(embedding.pending_records) }}</strong></div>
                <div class="inline-metric"><span>质量问题</span><strong>{{ exactNumber(summary.quality_findings) }}</strong></div>
                <div class="inline-metric"><span>已出库</span><strong>{{ exactNumber(summary.exports) }}</strong></div>
                <div class="inline-metric"><span>Benchmark 数据</span><strong>{{ exactNumber(summary.benchmark_rows) }}</strong></div>
            </div>

            <div class="subsection recent-exports" v-if="latestExports.length">
                <div class="subsection-title"><span>最近出湖</span><span>{{ latestExports.length }}</span></div>
                <div
                    v-for="item in latestExports"
                    :key="item.export_id || item.output_uri"
                    class="export-row"
                    :class="{ copied: copiedExport === item.output_uri }"
                >
                    <div class="export-main" :title="`${exportLabel(item)}：${item.output_uri}（点击复制）`" @click="copyText(item.output_uri)">
                        <span class="export-name">{{ exportLabel(item) }}</span>
                        <span class="export-path">{{ item.output_uri }}</span>
                        <span v-if="exportDesc(item)" class="export-desc">{{ exportDesc(item) }}</span>
                    </div>
                    <div class="export-actions" @click.stop>
                        <button type="button" class="export-btn" @click="previewExport(item)">预览</button>
                        <button type="button" class="export-btn" @click="downloadExport(item)">下载</button>
                    </div>
                </div>
            </div>
            <p v-else class="empty-line">暂无出湖产出</p>

            <p class="feedback-line" :title="warehouseText">{{ warehouseText }}</p>
        </section>

        <section class="status-column pipeline-column">
            <header class="column-header">
                <div>
                    <span class="column-kicker">02</span>
                    <h3>WebAgent + 流水线</h3>
                </div>
                <span :class="['state-label', stateLevel(pipeline.status)]">{{ pipelineStatusText }}</span>
            </header>

            <div class="agent-feedback">
                <span :class="['status-dot', stateLevel(acquisition?.state)]"></span>
                <div>
                    <strong>Obtainer / WebAgent</strong>
                    <p :title="acquisitionFeedback">{{ acquisitionFeedback }}</p>
                </div>
            </div>

            <div class="queue-table">
                <div class="queue-row queue-head">
                    <span>队列</span><span>待处理</span><span>运行中</span><span>已处理</span><span>丢弃</span><span>失败</span>
                </div>
                <div class="queue-row">
                    <strong>WebAgent</strong>
                    <span>{{ exactNumber(campaignQueue.pending) }}</span>
                    <span>{{ exactNumber(campaignQueue.running) }}</span>
                    <span>{{ exactNumber(campaignQueue.succeeded) }}</span>
                    <span>0</span>
                    <span :class="{ failed: numberValue(campaignQueue.failed) }">{{ exactNumber(campaignQueue.failed) }}</span>
                </div>
                <div v-for="item in pipelineQueues" :key="item.key || item.name" class="queue-row">
                    <strong :title="item.name">{{ queueLabel(item.name) }}</strong>
                    <span>{{ exactNumber(item.pending) }}</span>
                    <span>{{ exactNumber(item.running) }}</span>
                    <span>{{ exactNumber(item.processed) }}</span>
                    <span>{{ exactNumber(item.dropped) }}</span>
                    <span :class="{ failed: numberValue(item.failed) }">{{ exactNumber(item.failed) }}</span>
                </div>
                <div v-if="pipelineQueues.length === 0" class="queue-empty">暂无后处理队列</div>
            </div>

            <div class="subsection compact-metrics pipeline-totals">
                <div class="inline-metric"><span>WebAgent 总任务</span><strong>{{ exactNumber(campaignQueue.total) }}</strong></div>
                <div class="inline-metric"><span>流水线已选数据</span><strong>{{ exactNumber(pipeline.selected) }}</strong></div>
                <div class="inline-metric"><span>采集下载进度</span><strong>{{ exactNumber(acquisition?.download?.processed) }} / {{ exactNumber(acquisition?.download?.total) }}</strong></div>
            </div>

            <div class="feedback-list">
                <div v-for="(item, index) in pipelineFeedback" :key="`${item.source}-${index}`" class="feedback-row">
                    <span :class="['status-dot', stateLevel(item.state)]"></span>
                    <strong>{{ queueLabel(item.source) }}</strong>
                    <span :title="item.message">{{ item.message }}</span>
                </div>
                <p v-if="pipelineFeedback.length === 0" class="empty-line">等待 WebAgent 和持续流水线反馈</p>
            </div>
        </section>

        <section class="status-column dataflow-column">
            <header class="column-header">
                <div>
                    <span class="column-kicker">03</span>
                    <h3>DataFlowAgent</h3>
                </div>
                <span :class="['state-label', stateLevel(dataflow.state)]">{{ dataflowStateText }}</span>
            </header>

            <div class="agent-feedback">
                <span :class="['status-dot', stateLevel(dataflow.state)]"></span>
                <div>
                    <strong>{{ dataflowPhaseText }}</strong>
                    <p :title="dataflowFeedback">{{ dataflowFeedback }}</p>
                </div>
            </div>

            <div class="dataflow-counts">
                <div v-for="item in dataflowMetrics" :key="item.label" class="count-row">
                    <span>{{ item.label }}</span>
                    <strong :class="{ failed: item.failed && numberValue(item.value) }">{{ exactNumber(item.value) }}</strong>
                </div>
            </div>

            <div class="subsection dataflow-context">
                <div class="inline-metric"><span>当前 Operator</span><strong :title="dataflow.current_operator">{{ dataflow.current_operator || '-' }}</strong></div>
                <div class="inline-metric"><span>目标数据集</span><strong :title="dataflow.dataset">{{ dataflow.dataset || '-' }}</strong></div>
                <div class="inline-metric"><span>执行轮次</span><strong>{{ exactNumber(dataflow.attempt) }}</strong></div>
            </div>

            <div class="subsection task-feedback">
                <div class="subsection-title"><span>当前 Agent 反馈</span><span>{{ taskFeedback.length }}</span></div>
                <div v-for="item in taskFeedback" :key="item.key" class="task-row">
                    <span :class="['status-dot', item.level]"></span>
                    <strong>{{ item.source }}</strong>
                    <span :title="item.message">{{ item.message }}</span>
                </div>
                <p v-if="taskFeedback.length === 0" class="empty-line">暂无 DataFlowAgent 反馈</p>
            </div>

            <p v-if="dataflow.error" class="error-line" :title="dataflow.error">{{ dataflow.error }}</p>
        </section>

        <div v-if="previewState" class="export-preview-mask" @click.self="closePreview">
            <div class="export-preview-panel">
                <header class="export-preview-head">
                    <strong>出湖预览{{ previewState.file ? `：${previewState.file}` : '' }}</strong>
                    <button type="button" class="export-btn" @click="closePreview">关闭</button>
                </header>
                <div v-if="previewState.loading" class="export-preview-loading">加载中…</div>
                <div v-else-if="previewState.error" class="export-preview-error">{{ previewState.error }}</div>
                <div v-else>
                    <div v-if="previewState.description" class="export-preview-desc">
                        <p v-if="previewState.description.recipe_name">Recipe：{{ previewState.description.recipe_name }}</p>
                        <p v-if="previewState.description.records">记录数：{{ exactNumber(previewState.description.records) }}</p>
                        <p v-if="previewState.description.total_samples">目标样本：{{ exactNumber(previewState.description.total_samples) }}</p>
                        <p v-if="previewState.description.strategy">策略：{{ previewState.description.strategy }}</p>
                        <p v-if="previewState.description.buckets?.length">分桶：{{ previewState.description.buckets.join(' / ') }}</p>
                        <p v-if="previewState.description.snapshot_id">快照：{{ previewState.description.snapshot_id }}</p>
                    </div>
                    <table v-if="previewState.rows.length" class="export-preview-table">
                        <thead><tr><th v-for="c in previewState.columns" :key="c">{{ c }}</th></tr></thead>
                        <tbody>
                            <tr v-for="(row, i) in previewState.rows" :key="i">
                                <td v-for="c in previewState.columns" :key="c" :title="cellText(row[c])">{{ cellText(row[c]) }}</td>
                            </tr>
                        </tbody>
                    </table>
                    <p v-else class="empty-line">无数据</p>
                </div>
            </div>
        </div>
    </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useLoopAI } from '@/stores/loopAI'
import { getWebAgentOverview } from '@/services/obtainerLake'

const loopAI = useLoopAI()
const props = defineProps({
    running: {
        type: Boolean,
        default: false
    }
})
const emit = defineEmits(['active-change'])
const overview = ref(null)
const lastError = ref('')
let refreshTimer = null

const normalizeText = (value) => String(value || '').trim()
const numberValue = (value) => {
    const number = Number(value || 0)
    return Number.isFinite(number) ? number : 0
}
const exactNumber = (value) => new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 0 }).format(numberValue(value))
const taskId = computed(() => normalizeText(loopAI.currentTask?.task_id))
const taskStatus = computed(() => loopAI.taskStatus || {})
const taskState = computed(() => taskStatus.value.state || {})
const obtainerState = computed(() => taskState.value.obtainer || {})
const webcrawlerState = computed(() => taskState.value.webcrawler || {})
const requestedWarehouse = computed(() => normalizeText(
    obtainerState.value.warehouse || obtainerState.value.warehouse_root ||
    webcrawlerState.value.warehouse || webcrawlerState.value.warehouse_root
))
const requestedCampaignId = computed(() => normalizeText(
    obtainerState.value.webagent_campaign_id || obtainerState.value.campaign_id ||
    webcrawlerState.value.webagent_campaign_id || webcrawlerState.value.campaign_id
))

const initialized = computed(() => overview.value?.initialized === true)
const monitor = computed(() => overview.value?.monitor || {})
const summary = computed(() => monitor.value.summary || {})
const embedding = computed(() => monitor.value.embedding || {})
const layerRows = computed(() => overview.value?.layers || [
    { level: 'L1', label: '原始网页', count: 0, datasets: [] },
    { level: 'L2', label: '预处理网页', count: 0, datasets: [] },
    { level: 'L3', label: '初级 SFT', count: 0, datasets: [] }
])
const lakeMetrics = computed(() => [
    { label: '数据集', value: summary.value.datasets },
    { label: '总记录', value: summary.value.records },
    { label: '数据资产', value: summary.value.assets },
    { label: '入湖批次', value: summary.value.ingest_runs }
])
const warehouseText = computed(() => {
    if (!initialized.value) return lastError.value || '当前任务尚未初始化数据湖'
    return overview.value?.warehouse || '数据湖已绑定'
})

const acquisition = computed(() => overview.value?.acquisition || null)
const campaign = computed(() => overview.value?.campaign || null)
const campaignQueue = computed(() => campaign.value?.queue || {})
const pipeline = computed(() => overview.value?.pipeline || {})
const pipelineQueues = computed(() => Array.isArray(pipeline.value.queues) ? pipeline.value.queues : [])
const pipelineFeedback = computed(() => Array.isArray(pipeline.value.feedback) ? pipeline.value.feedback.slice(0, 5) : [])
const pipelineStatusText = computed(() => ({
    running: '持续运行', queued: '启动中', paused: '已暂停', completed: '已完成',
    completed_with_errors: '部分失败', failed: '失败', idle: '等待启动'
}[normalizeText(pipeline.value.status).toLowerCase()] || '等待启动'))
const acquisitionFeedback = computed(() => acquisition.value?.phase_detail || (
    initialized.value ? '等待 WebAgent 采集反馈' : '当前任务尚未启动 Obtainer'
))

const dataflow = computed(() => overview.value?.dataflow_agent || {})
const latestExports = computed(() => {
    const rows = monitor.value.latest?.exports
    return Array.isArray(rows) ? rows.slice(0, 3) : []
})
const exportLabel = (item) => {
    const strategy = normalizeText(item?.strategy).toLowerCase()
    if (['recipe_export', 'recipe'].includes(strategy)) return 'Recipe 出湖'
    if (['export_jsonl', 'export-jsonl'].includes(strategy)) return 'JSONL 出湖'
    if (['stratified', 'random', 'sample'].includes(strategy)) return '抽样出湖'
    return '出湖'
}
const copiedExport = ref('')
const previewState = ref(null)
const exportDesc = (item) => {
    const d = item.description || {}
    const parts = []
    if (d.records) parts.push(`${exactNumber(d.records)} 条`)
    if (d.total_samples) parts.push(`目标 ${exactNumber(d.total_samples)}`)
    if (d.recipe_name) parts.push(d.recipe_name)
    if (Array.isArray(d.buckets) && d.buckets.length) parts.push(d.buckets.join(' / '))
    return parts.join(' · ')
}
const cellText = (v) => {
    const s = v === null || v === undefined ? '' : String(v)
    return s.length > 80 ? `${s.slice(0, 80)}…` : s
}
const previewExport = async (item) => {
    previewState.value = { loading: true, description: item.description || null, columns: [], rows: [], error: '', file: '' }
    try {
        const url = `/api/obtainer/export/preview?uri=${encodeURIComponent(item.output_uri || '')}&limit=20`
        const res = await fetch(url)
        const data = await res.json()
        const payload = data?.data || {}
        previewState.value = {
            loading: false,
            description: payload.description || item.description || null,
            columns: payload.columns || [],
            rows: payload.rows || [],
            error: data?.message || '',
            file: payload.file || ''
        }
    } catch (err) {
        previewState.value = { loading: false, description: null, columns: [], rows: [], error: err?.message || '预览失败', file: '' }
    }
}
const closePreview = () => { previewState.value = null }
const downloadExport = (item) => {
    const uri = item.output_uri || ''
    if (!uri) return
    window.open(`/api/obtainer/export/download?uri=${encodeURIComponent(uri)}`, '_blank')
}
let copyTimer = null
const copyText = async (text) => {
    const value = normalizeText(text)
    if (!value) return
    try {
        if (navigator.clipboard?.writeText) {
            await navigator.clipboard.writeText(value)
        } else {
            const area = document.createElement('textarea')
            area.value = value
            area.style.position = 'fixed'
            area.style.opacity = '0'
            document.body.appendChild(area)
            area.select()
            document.execCommand('copy')
            document.body.removeChild(area)
        }
        copiedExport.value = value
        if (copyTimer) window.clearTimeout(copyTimer)
        copyTimer = window.setTimeout(() => { copiedExport.value = '' }, 1600)
    } catch (error) {
        // ignore clipboard failures
    }
}
const operationalActive = computed(() => {
    const campaignStatus = normalizeText(campaign.value?.status).toLowerCase()
    const pipelineStatus = normalizeText(pipeline.value?.status).toLowerCase()
    const dataflowStatus = normalizeText(dataflow.value?.state).toLowerCase()
    const backgroundActive = Boolean(
        acquisition.value?.active ||
        ['queued', 'running'].includes(campaignStatus) ||
        ['queued', 'running'].includes(pipelineStatus) ||
        dataflowStatus === 'running'
    )
    return props.running || backgroundActive
})
const dataflowStateText = computed(() => ({
    running: '运行中', completed: '已完成', completed_with_errors: '部分失败',
    failed: '失败', idle: '等待任务'
}[normalizeText(dataflow.value.state).toLowerCase()] || '等待任务'))
const dataflowPhaseText = computed(() => ({
    exporting: '导出试运行数据', planning: '规划处理链路', running: '执行处理链路',
    validating: '校验处理结果', applying: '写回数据湖', finalizing: '整理处理结果',
    completed: '处理完成', failed: '处理失败', waiting: '等待处理任务'
}[normalizeText(dataflow.value.phase).toLowerCase()] || '等待处理任务'))
const dataflowFeedback = computed(() => dataflow.value.feedback || '当前没有 DataFlowAgent 运行')
const dataflowMetrics = computed(() => [
    { label: '选中数据', value: dataflow.value.selected_rows },
    { label: '输入数据', value: dataflow.value.input_rows },
    { label: '输出数据', value: dataflow.value.output_rows },
    { label: '丢弃数据', value: dataflow.value.dropped_rows },
    { label: '失败数据', value: dataflow.value.failed_rows, failed: true },
    { label: '已应用数据', value: dataflow.value.applied_rows }
])

const stateLevel = (state) => {
    const value = normalizeText(state).toLowerCase()
    if (['running', 'queued'].includes(value)) return 'running'
    if (['completed', 'succeeded', 'success', 'online'].includes(value)) return 'good'
    if (['failed', 'completed_with_errors', 'interrupted', 'error'].includes(value)) return 'bad'
    return 'idle'
}
const queueLabel = (name) => ({
    webpage_to_pt: '正文提取', domain_classify: '领域分类', pt_to_sft_qa: 'SFT QA',
    pt_to_sft_code: '代码 SFT', pt_to_sft_text2sql: 'Text2SQL', sft_validate: 'SFT 校验',
    webagent: 'WebAgent'
}[normalizeText(name)] || normalizeText(name) || '流水线')

const taskFeedback = computed(() => {
    const info = taskStatus.value.custom_info || {}
    return Object.entries(info)
        .filter(([key, value]) => {
            const text = `${key} ${value?.message || ''} ${value?.current || ''}`.toLowerCase()
            return text.includes('dataflow') || text.includes('obtainer')
        })
        .slice(-5)
        .reverse()
        .map(([key, value]) => ({
            key,
            source: key.split('.').slice(-1)[0] || 'agent',
            message: value?.message || value?.current || key,
            level: value?.data?.error ? 'bad' : 'running'
        }))
})

const loadOverview = async () => {
    const requestedTaskId = taskId.value
    if (!requestedTaskId) {
        overview.value = null
        return
    }
    try {
        const response = await getWebAgentOverview({
            lake: '.datamixer/lake.yaml',
            task_id: requestedTaskId,
            root: requestedWarehouse.value || undefined,
            run_id: requestedCampaignId.value || undefined
        })
        if (taskId.value !== requestedTaskId) return
        overview.value = response?.data || null
        lastError.value = response?.data?.error || ''
    } catch (error) {
        if (taskId.value !== requestedTaskId) return
        overview.value = null
        lastError.value = error?.message || '状态读取失败'
    }
}

onMounted(() => {
    loadOverview()
    refreshTimer = window.setInterval(loadOverview, 10000)
})

watch([taskId, requestedWarehouse, requestedCampaignId], () => {
    overview.value = null
    lastError.value = ''
    loadOverview()
})

watch(operationalActive, (active) => emit('active-change', active), { immediate: true })

onBeforeUnmount(() => {
    if (refreshTimer) window.clearInterval(refreshTimer)
})
</script>

<style lang="scss" scoped>
.obtainer-status-panel {
    width: 960px;
    min-height: 350px;
    display: grid;
    grid-template-columns: 0.92fr 1.28fr 0.92fr;
    color: var(--lp-text);

    &.active {
        box-shadow: inset 0 3px 0 rgba(217, 144, 0, 0.82);
    }
}

.status-column {
    min-width: 0;
    padding: 12px 14px 14px;
    border-left: 1px solid rgba(90, 45, 133, 0.12);

    &:first-child { border-left: 0; }
}

.column-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 8px;
    min-height: 38px;
    margin-bottom: 12px;

    > div { display: flex; align-items: baseline; gap: 7px; min-width: 0; }
    h3 { margin: 0; font-size: 13px; line-height: 20px; font-weight: 700; letter-spacing: 0; white-space: nowrap; }
}

.column-kicker { font-size: 9px; font-weight: 700; color: rgba(90, 45, 133, 0.56); }
.state-label { font-size: 10px; line-height: 20px; font-weight: 700; white-space: nowrap; }
.state-label.good { color: #168258; }
.state-label.running { color: #9a6500; }
.state-label.bad { color: #b4232a; }
.state-label.idle { color: #767676; }

.metric-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    border-top: 1px solid rgba(90, 45, 133, 0.12);
    border-left: 1px solid rgba(90, 45, 133, 0.12);
}
.metric-item {
    min-width: 0;
    padding: 8px;
    border-right: 1px solid rgba(90, 45, 133, 0.12);
    border-bottom: 1px solid rgba(90, 45, 133, 0.12);
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 6px;
    span { font-size: 10px; color: rgba(70, 70, 70, 0.68); }
    strong { font-size: 14px; font-weight: 700; }
}

.subsection { margin-top: 12px; padding-top: 9px; border-top: 1px solid rgba(90, 45, 133, 0.12); }
.subsection-title { display: flex; justify-content: space-between; margin-bottom: 5px; font-size: 10px; font-weight: 700; color: rgba(65, 65, 65, 0.72); }
.level-row { display: grid; grid-template-columns: 30px minmax(0, 1fr) 68px; gap: 6px; align-items: center; min-height: 26px; font-size: 11px; }
.level-name { font-weight: 800; color: rgba(90, 45, 133, 0.88); }
.level-detail { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: rgba(65, 65, 65, 0.7); }
.level-row strong { text-align: right; }

.compact-metrics { display: grid; gap: 5px; }
.inline-metric { display: grid; grid-template-columns: minmax(0, 1fr) minmax(55px, auto); gap: 8px; min-height: 20px; align-items: center; font-size: 10px; }
.inline-metric span { color: rgba(65, 65, 65, 0.68); }
.inline-metric strong { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; text-align: right; font-size: 11px; }

.feedback-line, .empty-line, .error-line { margin: 10px 0 0; font-size: 10px; color: rgba(65, 65, 65, 0.62); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.error-line { color: #b4232a; }

.recent-exports { display: grid; gap: 4px; }
.export-row { display: grid; grid-template-columns: 66px minmax(0, 1fr); gap: 6px; align-items: center; min-height: 24px; padding: 3px 6px; border-radius: 4px; background: rgba(90, 45, 133, 0.04); cursor: pointer; transition: background 0.15s ease; }
.export-row:hover { background: rgba(90, 45, 133, 0.1); }
.export-row.copied { background: rgba(33, 163, 102, 0.12); }
.export-name { font-size: 10px; font-weight: 700; color: rgba(90, 45, 133, 0.82); white-space: nowrap; }
.export-path { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 10px; color: rgba(65, 65, 65, 0.72); }

.agent-feedback { display: grid; grid-template-columns: 8px minmax(0, 1fr); gap: 7px; align-items: start; min-height: 42px; }
.agent-feedback strong { display: block; font-size: 11px; line-height: 14px; }
.agent-feedback p { margin: 3px 0 0; font-size: 10px; color: rgba(65, 65, 65, 0.66); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.status-dot { width: 7px; height: 7px; margin-top: 4px; border-radius: 50%; background: #8a8a8a; }
.status-dot.good { background: #21a366; }
.status-dot.running { background: #d99000; }
.status-dot.bad { background: #d13438; }

.queue-table { margin-top: 4px; border: 1px solid rgba(90, 45, 133, 0.12); overflow: hidden; }
.queue-row { display: grid; grid-template-columns: minmax(72px, 1.5fr) repeat(5, minmax(38px, 0.65fr)); min-height: 27px; align-items: center; border-top: 1px solid rgba(90, 45, 133, 0.08); font-size: 10px; }
.queue-row:first-child { border-top: 0; }
.queue-row > * { min-width: 0; padding: 5px 4px; text-align: right; }
.queue-row > :first-child { text-align: left; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.queue-head { background: rgba(90, 45, 133, 0.05); color: rgba(65, 65, 65, 0.62); font-size: 9px; }
.queue-empty { padding: 7px; border-top: 1px solid rgba(90, 45, 133, 0.08); font-size: 10px; color: rgba(65, 65, 65, 0.58); }
.failed { color: #b4232a; }
.pipeline-totals { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.pipeline-totals .inline-metric { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; }
.pipeline-totals .inline-metric strong { text-align: left; }

.feedback-list { display: grid; gap: 5px; margin-top: 10px; }
.feedback-row, .task-row { display: grid; grid-template-columns: 7px 68px minmax(0, 1fr); gap: 6px; align-items: center; font-size: 10px; }
.feedback-row .status-dot, .task-row .status-dot { margin-top: 0; }
.feedback-row strong, .task-row strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: rgba(90, 45, 133, 0.82); }
.feedback-row > span:last-child, .task-row > span:last-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: rgba(65, 65, 65, 0.65); }

.dataflow-counts { margin-top: 4px; border-top: 1px solid rgba(90, 45, 133, 0.12); }
.count-row { display: flex; align-items: baseline; justify-content: space-between; min-height: 34px; padding: 7px 2px; border-bottom: 1px solid rgba(90, 45, 133, 0.09); }
.count-row span { font-size: 10px; color: rgba(65, 65, 65, 0.7); }
.count-row strong { font-size: 15px; }
.dataflow-context .inline-metric { grid-template-columns: 105px minmax(0, 1fr); }
.task-feedback { max-height: 108px; overflow: hidden; }


.export-row { display: flex; flex-direction: column; gap: 4px; padding: 6px 2px; border-bottom: 1px solid rgba(90, 45, 133, 0.09); }
.export-main { cursor: pointer; display: grid; gap: 2px; }
.export-desc { font-size: 10px; color: rgba(65, 65, 65, 0.55); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.export-actions { display: flex; gap: 6px; }
.export-btn { font-size: 10px; padding: 2px 8px; border: 1px solid rgba(90, 45, 133, 0.35); border-radius: 4px; background: rgba(90, 45, 133, 0.06); color: rgba(90, 45, 133, 0.85); cursor: pointer; }
.export-btn:hover { background: rgba(90, 45, 133, 0.14); }
.export-preview-mask { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.35); z-index: 9999; display: flex; align-items: center; justify-content: center; }
.export-preview-panel { background: var(--lp-surface); border-radius: 8px; width: min(860px, 90vw); max-height: 80vh; display: flex; flex-direction: column; overflow: hidden; box-shadow: 0 8px 30px rgba(0, 0, 0, 0.25); }
.export-preview-head { display: flex; align-items: center; justify-content: space-between; padding: 10px 14px; border-bottom: 1px solid rgba(90, 45, 133, 0.15); }
.export-preview-head strong { font-size: 13px; color: rgba(90, 45, 133, 0.95); }
.export-preview-loading, .export-preview-error { padding: 18px; font-size: 12px; color: rgba(65, 65, 65, 0.75); }
.export-preview-error { color: #c0392b; }
.export-preview-desc { padding: 10px 14px; background: rgba(90, 45, 133, 0.04); border-bottom: 1px solid rgba(90, 45, 133, 0.1); font-size: 11px; color: rgba(65, 65, 65, 0.8); display: grid; gap: 3px; }
.export-preview-desc p { margin: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.export-preview-table { width: 100%; border-collapse: collapse; font-size: 11px; }
.export-preview-table th, .export-preview-table td { border: 1px solid rgba(90, 45, 133, 0.12); padding: 4px 6px; text-align: left; max-width: 240px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.export-preview-table th { background: rgba(90, 45, 133, 0.08); position: sticky; top: 0; }
.export-preview-panel > div:last-child { overflow: auto; }
@media (max-width: 1050px) {
    .obtainer-status-panel { width: 860px; grid-template-columns: 0.9fr 1.25fr 0.9fr; }
    .status-column { padding-left: 10px; padding-right: 10px; }
}
</style>
