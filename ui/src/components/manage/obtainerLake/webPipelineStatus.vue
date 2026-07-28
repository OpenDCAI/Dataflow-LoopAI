<template>
    <aside v-if="isAvailable" class="web-pipeline-status" :class="{ collapsed, embedded }">
        <header class="panel-header">
            <div class="header-copy">
                <span class="live-dot" :class="overviewState"></span>
                <div>
                    <h2>Obtainer 数据采集流水线</h2>
                    <p>{{ headerSubtitle }}</p>
                </div>
            </div>
            <div class="header-actions">
                <button class="icon-button" title="刷新运行状态" @click="refresh" :disabled="loading">
                    <i class="ms-Icon ms-Icon--Refresh" :class="{ spinning: loading }"></i>
                </button>
                <button
                    class="icon-button"
                    :title="collapsed ? '展开流水线状态' : '收起流水线状态'"
                    @click="collapsed = !collapsed"
                >
                    <i class="ms-Icon" :class="collapsed ? 'ms-Icon--ChevronLeft' : 'ms-Icon--ChevronRight'"></i>
                </button>
            </div>
        </header>

        <div v-if="!collapsed" class="panel-content">
            <div v-if="error" class="panel-error">
                <i class="ms-Icon ms-Icon--StatusErrorFull"></i>
                <span>{{ error }}</span>
            </div>

            <template v-else-if="overview">
                <section v-if="acquisition" class="campaign-context acquisition-context">
                    <div class="eyebrow">数据集获取主 Agent</div>
                    <p class="campaign-query" :title="acquisition.objective || acquisition.run_dir">
                        {{ acquisition.objective || shortPath(acquisition.run_dir) }}
                    </p>
                    <div class="campaign-meta">
                        <span class="status-badge" :class="acquisitionState">{{ acquisitionStatusText }}</span>
                        <span>{{ acquisitionPhaseText }}</span>
                        <span v-if="!acquisition.bound_to_lake" class="unbound-note">未绑定当前湖</span>
                    </div>
                    <div v-if="acquisition.download?.total" class="acquisition-progress">
                        <span>下载 {{ acquisition.download.processed || 0 }}/{{ acquisition.download.total }}</span>
                        <span>{{ acquisition.download.completed || 0 }} 成功 · {{ acquisition.download.failed || 0 }} 失败</span>
                    </div>
                </section>

                <section v-if="otherActiveAcquisitions.length" class="section-block acquisition-warning">
                    <div class="section-heading">
                        <span>其他运行中的获取任务</span>
                        <small>{{ otherActiveAcquisitions.length }}</small>
                    </div>
                    <div v-for="item in otherActiveAcquisitions" :key="item.run_dir" class="unbound-run">
                        <strong>{{ item.phase_detail }}</strong>
                        <span :title="item.run_dir">{{ shortPath(item.run_dir) }}</span>
                    </div>
                </section>

                <section v-if="campaign" class="campaign-context">
                    <div class="eyebrow">垂域网页采集 Agent</div>
                    <p class="campaign-query" :title="campaign.root_query">{{ campaign.root_query }}</p>
                    <div class="campaign-meta">
                        <span class="status-badge" :class="campaignState">{{ campaignStatusText }}</span>
                        <span>{{ campaign.webagent }}</span>
                        <span :title="campaign.dataset">{{ campaign.dataset || '未指定数据集' }}</span>
                    </div>
                </section>

                <section class="section-block">
                    <div class="section-heading">
                        <span>数据分层</span>
                        <small>{{ overview.domain_classes || 0 }} 个领域类</small>
                    </div>
                    <div class="layer-list">
                        <div v-for="layer in layers" :key="layer.level" class="layer-row">
                            <div class="layer-level" :class="layer.level.toLowerCase()">{{ layer.level }}</div>
                            <div class="layer-main">
                                <div class="layer-line">
                                    <strong>{{ layer.label }}</strong>
                                    <b>{{ formatNumber(layer.count) }}</b>
                                </div>
                                <p :title="layer.description">{{ layer.description }}</p>
                                <div v-if="layer.datasets?.length" class="dataset-line" :title="layer.datasets.join(', ')">
                                    {{ layer.datasets.join(' · ') }}
                                </div>
                            </div>
                        </div>
                    </div>
                </section>

                <section class="section-block">
                    <div class="section-heading">
                        <span>处理进度</span>
                        <small>{{ activeStageTitle }}</small>
                    </div>
                    <div class="stage-list">
                        <div v-for="stage in stages" :key="stage.name" class="stage-row">
                            <span class="stage-dot" :class="stage.state"></span>
                            <div class="stage-copy">
                                <div class="stage-line">
                                    <strong>{{ stage.title }}</strong>
                                    <span :class="['stage-state', stage.state]">{{ stageStateText(stage.state) }}</span>
                                </div>
                                <p :title="stage.detail">{{ stage.detail }}</p>
                                <div v-if="stage.progress !== null && stage.progress !== undefined" class="mini-progress">
                                    <span :style="{ width: `${Math.round(stage.progress * 100)}%` }"></span>
                                </div>
                            </div>
                        </div>
                    </div>
                </section>

                <section v-if="campaign" class="section-block">
                    <div class="section-heading">
                        <span>WebAgent 队列</span>
                        <small>{{ campaign.configured_workers || 0 }} workers</small>
                    </div>
                    <div class="queue-grid">
                        <div v-for="item in queueItems" :key="item.key" class="queue-cell" :class="item.key">
                            <b>{{ item.value }}</b>
                            <span>{{ item.label }}</span>
                        </div>
                    </div>
                    <div class="worker-list">
                        <div v-for="worker in workers" :key="worker.worker_id" class="worker-row">
                            <span class="worker-dot"></span>
                            <strong>{{ worker.worker_id }}</strong>
                            <span class="worker-query" :title="worker.query">{{ worker.query }}</span>
                        </div>
                        <div v-if="workers.length === 0" class="empty-line">暂无运行中的 worker</div>
                    </div>
                </section>

                <section v-if="activeWork.length" class="section-block active-work">
                    <div class="section-heading">
                        <span>当前进行中</span>
                        <small>{{ activeWork.length }}</small>
                    </div>
                    <div v-for="item in activeWork" :key="item.key" class="work-row">
                        <span class="work-source">{{ item.source }}</span>
                        <span class="work-message" :title="item.message">{{ item.message }}</span>
                    </div>
                </section>

                <section v-if="recentTasks.length" class="section-block recent-work">
                    <div class="section-heading">
                        <span>最近子任务</span>
                        <small>{{ recentTasks.length }}</small>
                    </div>
                    <div v-for="task in recentTasks" :key="task.task_id" class="recent-row">
                        <span class="task-status" :class="task.status">{{ taskStatusText(task.status) }}</span>
                        <span class="recent-query" :title="task.query">{{ task.query }}</span>
                    </div>
                </section>
            </template>

            <div v-else class="loading-line">
                <i class="ms-Icon ms-Icon--Sync"></i>
                正在读取当前数据湖与 WebAgent 状态…
            </div>
        </div>
    </aside>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { getWebAgentOverview } from '@/services/obtainerLake'

const props = defineProps({
    taskId: {
        type: String,
        default: ''
    },
    taskStatus: {
        type: Object,
        default: () => ({})
    },
    lake: {
        type: String,
        default: ''
    },
    root: {
        type: String,
        default: ''
    },
    runId: {
        type: String,
        default: ''
    },
    embedded: {
        type: Boolean,
        default: false
    }
})

const overview = ref(null)
const loading = ref(false)
const error = ref('')
const collapsed = ref(false)
let refreshTimer = null

const layers = computed(() => overview.value?.layers || [])
const stages = computed(() => overview.value?.stages || [])
const campaign = computed(() => overview.value?.campaign || null)
const acquisition = computed(() => overview.value?.acquisition || null)
const otherActiveAcquisitions = computed(() => overview.value?.other_active_acquisitions || [])
const workers = computed(() => campaign.value?.workers || [])
const recentTasks = computed(() => campaign.value?.recent_tasks || [])
const taskState = computed(() => props.taskStatus?.state || {})
const taskObtainer = computed(() => taskState.value?.obtainer || {})
const taskWebCrawler = computed(() => taskState.value?.webcrawler || {})
const isAvailable = computed(() => Boolean(props.taskId || props.lake || props.root))
const requestedWarehouse = computed(() => {
    return normalize(
        props.root ||
        taskObtainer.value?.warehouse ||
        taskObtainer.value?.warehouse_root ||
        taskWebCrawler.value?.warehouse ||
        taskWebCrawler.value?.warehouse_root
    )
})
const requestedCampaignId = computed(() => {
    return normalize(
        props.runId ||
        taskObtainer.value?.webagent_campaign_id ||
        taskObtainer.value?.campaign_id ||
        taskWebCrawler.value?.webagent_campaign_id ||
        taskWebCrawler.value?.campaign_id
    )
})

const normalize = (value) => String(value || '').trim()
const shorten = (value, limit = 120) => {
    const text = normalize(value)
    return text.length > limit ? `${text.slice(0, limit - 1)}…` : text
}
const formatNumber = (value) => new Intl.NumberFormat('zh-CN').format(Number(value || 0))
const shortPath = (value) => {
    const text = normalize(value)
    if (!text) return '-'
    const parts = text.split('/').filter(Boolean)
    return parts.length <= 3 ? text : `.../${parts.slice(-3).join('/')}`
}

const campaignState = computed(() => {
    const status = normalize(campaign.value?.status).toLowerCase()
    if (['running', 'queued'].includes(status)) return 'running'
    if (status === 'paused') return 'paused'
    if (['failed', 'completed_with_errors'].includes(status)) return 'warning'
    if (status === 'completed') return 'completed'
    return 'idle'
})

const acquisitionState = computed(() => {
    if (!acquisition.value) return 'idle'
    if (acquisition.value.active) return 'running'
    const status = normalize(acquisition.value.state).toLowerCase()
    if (status === 'completed') return 'completed'
    if (['failed', 'interrupted'].includes(status)) return 'warning'
    return 'idle'
})

const overviewState = computed(() => {
    return acquisition.value?.active ? acquisitionState.value : campaignState.value
})

const acquisitionStatusText = computed(() => {
    if (!acquisition.value) return ''
    if (acquisition.value.active) return '运行中'
    return ({ completed: '已完成', failed: '失败', interrupted: '已中断' }[
        normalize(acquisition.value.state).toLowerCase()
    ] || '未运行')
})

const acquisitionPhaseText = computed(() => acquisition.value?.phase_detail || '正在读取获取任务状态')

const campaignStatusText = computed(() => ({
    running: '运行中',
    queued: '排队中',
    paused: '已暂停',
    completed: '已完成',
    completed_with_errors: '完成但有错误',
    failed: '失败'
}[normalize(campaign.value?.status).toLowerCase()] || '未运行'))

const headerSubtitle = computed(() => {
    if (acquisition.value?.active) return `主 Agent 运行中 · ${acquisitionPhaseText.value}`
    if (!campaign.value) return '尚未发现 WebAgent campaign'
    const queue = campaign.value.queue || {}
    if (campaignState.value === 'running') {
        return `${queue.running || 0} worker 执行中 · ${queue.pending || 0} 等待中`
    }
    return `${campaignStatusText.value} · ${campaign.value.run_id}`
})

const queueItems = computed(() => {
    const queue = campaign.value?.queue || {}
    return [
        { key: 'pending', label: '等待', value: queue.pending || 0 },
        { key: 'running', label: '运行', value: queue.running || 0 },
        { key: 'succeeded', label: '成功', value: queue.succeeded || 0 },
        { key: 'failed', label: '失败', value: queue.failed || 0 }
    ]
})

const activeStageTitle = computed(() => {
    const active = stages.value.find((stage) => stage.state === 'running')
    return active ? active.title : '等待下一步'
})

const taskEvents = computed(() => {
    const entries = Object.entries(props.taskStatus?.custom_info || {})
    return entries
        .map(([key, value]) => ({ key, value: value || {} }))
        .filter(({ key, value }) => {
            const text = `${key} ${value.current || ''} ${value.message || ''}`.toLowerCase()
            return ['obtainer', 'webagent', 'webcrawler', 'dataflow', 'pipeline', 'domain_classify', 'mineru'].some((name) => text.includes(name))
        })
        .slice(-5)
        .reverse()
        .map(({ key, value }) => ({
            key: `event-${key}`,
            source: key.split('.').slice(-1)[0] || 'agent',
            message: shorten(value.message || value.current || key)
        }))
})

const activeWork = computed(() => {
    const work = []
    if (acquisition.value?.active) {
        work.push({
            key: `acquisition-${acquisition.value.run_dir}`,
            source: '数据集获取',
            message: acquisitionPhaseText.value
        })
    }
    const activeStage = stages.value.find((stage) => stage.state === 'running')
    if (activeStage) {
        work.push({ key: `stage-${activeStage.name}`, source: activeStage.title, message: activeStage.detail })
    }
    for (const worker of workers.value) {
        work.push({ key: `worker-${worker.worker_id}`, source: worker.worker_id, message: worker.query || worker.goal })
    }
    return [...work, ...taskEvents.value].slice(0, 6)
})

const stageStateText = (state) => ({
    running: '进行中', completed: '完成', waiting: '等待', warning: '需关注'
}[state] || '等待')

const taskStatusText = (status) => ({
    pending: '等待', running: '运行', succeeded: '成功', failed: '失败'
}[status] || status || '未知')

const refresh = async () => {
    if (!isAvailable.value) return
    loading.value = true
    try {
        const params = requestedWarehouse.value
            ? { root: requestedWarehouse.value }
            : { lake: normalize(props.lake) || '.loopai/lake.yaml' }
        if (requestedCampaignId.value) params.run_id = requestedCampaignId.value
        const result = await getWebAgentOverview(params)
        if (result?.code !== 200) throw new Error(result?.message || '无法读取 WebAgent 状态')
        overview.value = result.data
        error.value = ''
    } catch (reason) {
        error.value = shorten(reason?.message || '无法读取当前数据湖状态', 180)
    } finally {
        loading.value = false
    }
}

watch(
    () => [props.taskId, props.lake, props.root, props.runId],
    () => refresh(),
    { immediate: true }
)

onMounted(() => {
    refreshTimer = window.setInterval(refresh, 6000)
})

onBeforeUnmount(() => {
    if (refreshTimer) window.clearInterval(refreshTimer)
})
</script>

<style lang="scss" scoped>
.web-pipeline-status {
    position: absolute;
    top: 92px;
    right: 18px;
    width: min(390px, calc(100% - 36px));
    max-height: calc(100% - 350px);
    color: rgba(36, 39, 48, 1);
    background: rgba(255, 255, 255, 0.92);
    border: 1px solid rgba(95, 99, 116, 0.18);
    border-radius: 8px;
    box-shadow: 0 10px 28px rgba(32, 36, 52, 0.13);
    backdrop-filter: blur(14px);
    z-index: 4;
    overflow: hidden;

    &.embedded {
        position: relative;
        top: auto;
        right: auto;
        width: 100%;
        max-height: none;
        margin: 0 0 10px;
        box-shadow: 0 12px 26px rgba(28, 32, 40, 0.055);

        &.collapsed {
            width: 100%;
        }

        .panel-content {
            max-height: min(620px, calc(100vh - 270px));
        }
    }

    &.collapsed {
        width: 244px;
    }

    .panel-header {
        min-height: 54px;
        padding: 10px 12px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        border-bottom: 1px solid rgba(95, 99, 116, 0.12);
    }

    .header-copy {
        min-width: 0;
        display: flex;
        align-items: center;
        gap: 8px;

        h2, p { margin: 0; }
        h2 { font-size: 13px; line-height: 18px; font-weight: 700; }
        p { max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: rgba(96, 100, 112, 1); font-size: 10px; }
    }

    .live-dot, .stage-dot, .worker-dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        flex: 0 0 auto;
        background: rgba(154, 160, 166, 1);
    }
    .live-dot.running, .stage-dot.running { background: rgba(35, 135, 235, 1); box-shadow: 0 0 0 4px rgba(35, 135, 235, 0.14); animation: breathe 1.5s ease-in-out infinite; }
    .live-dot.completed, .stage-dot.completed, .worker-dot { background: rgba(42, 159, 109, 1); }
    .live-dot.warning, .stage-dot.warning { background: rgba(220, 125, 25, 1); }
    .live-dot.paused { background: rgba(123, 100, 194, 1); }

    .header-actions { display: flex; gap: 2px; }
    .icon-button { width: 27px; height: 27px; border: 0; border-radius: 5px; color: rgba(76, 80, 91, 1); background: transparent; cursor: pointer; }
    .icon-button:hover { background: rgba(94, 101, 126, 0.1); }
    .icon-button:disabled { opacity: .45; cursor: default; }
    .spinning { display: inline-block; animation: rotate .8s linear infinite; }

    .panel-content { max-height: calc(100vh - 450px); overflow-y: auto; padding: 0 12px 12px; }
    .campaign-context { padding: 11px 0 9px; border-bottom: 1px solid rgba(95, 99, 116, .1); }
    .eyebrow { margin-bottom: 4px; color: rgba(104, 107, 118, 1); font-size: 10px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; }
    .campaign-query { margin: 0; color: rgba(46, 49, 61, 1); font-size: 12px; line-height: 18px; }
    .campaign-meta { display: flex; align-items: center; gap: 6px; margin-top: 7px; color: rgba(103, 107, 118, 1); font-size: 10px; overflow: hidden; white-space: nowrap; }
    .campaign-meta > span:last-child { overflow: hidden; text-overflow: ellipsis; }
    .acquisition-progress { display: flex; justify-content: space-between; gap: 8px; margin-top: 7px; color: rgba(75, 80, 93, 1); font-size: 10px; }
    .unbound-note { color: rgba(154, 80, 6, 1); font-weight: 700; }
    .status-badge, .stage-state, .task-status { border-radius: 4px; padding: 2px 5px; font-size: 10px; line-height: 14px; }
    .status-badge.running, .stage-state.running { color: rgba(12, 92, 180, 1); background: rgba(35, 135, 235, .12); }
    .status-badge.completed, .stage-state.completed { color: rgba(20, 115, 74, 1); background: rgba(42, 159, 109, .12); }
    .status-badge.warning, .stage-state.warning { color: rgba(154, 80, 6, 1); background: rgba(220, 125, 25, .13); }
    .status-badge.paused { color: rgba(88, 64, 161, 1); background: rgba(123, 100, 194, .13); }

    .section-block { padding: 11px 0; border-bottom: 1px solid rgba(95, 99, 116, .1); }
    .section-block:last-child { border-bottom: 0; }
    .section-heading { display: flex; justify-content: space-between; margin-bottom: 8px; color: rgba(58, 62, 73, 1); font-size: 12px; font-weight: 700; }
    .section-heading small { color: rgba(116, 120, 131, 1); font-size: 10px; font-weight: 400; }
    .acquisition-warning { background: rgba(220, 125, 25, .045); }
    .unbound-run { display: grid; gap: 3px; padding: 6px 0; color: rgba(82, 85, 96, 1); font-size: 10px; }
    .unbound-run span { overflow: hidden; color: rgba(120, 100, 78, 1); text-overflow: ellipsis; white-space: nowrap; }

    .layer-list { display: grid; gap: 6px; }
    .layer-row { display: flex; gap: 8px; padding: 7px; background: rgba(80, 87, 112, .045); border-radius: 5px; }
    .layer-level { width: 29px; height: 24px; display: grid; place-items: center; border-radius: 4px; color: white; font-size: 10px; font-weight: 700; }
    .layer-level.l1 { background: rgba(61, 121, 190, 1); }
    .layer-level.l2 { background: rgba(114, 93, 183, 1); }
    .layer-level.l3 { background: rgba(35, 143, 106, 1); }
    .layer-main { min-width: 0; flex: 1; }
    .layer-line, .stage-line { display: flex; justify-content: space-between; gap: 8px; }
    .layer-line strong, .stage-line strong { font-size: 11px; }
    .layer-line b { color: rgba(45, 109, 188, 1); font-size: 12px; }
    .layer-main p, .stage-copy p { margin: 2px 0 0; overflow: hidden; color: rgba(104, 109, 120, 1); font-size: 10px; line-height: 14px; text-overflow: ellipsis; white-space: nowrap; }
    .dataset-line { margin-top: 3px; overflow: hidden; color: rgba(89, 93, 104, 1); font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }

    .stage-list { display: grid; gap: 8px; }
    .stage-row { display: flex; gap: 9px; align-items: flex-start; }
    .stage-dot { margin-top: 4px; width: 7px; height: 7px; }
    .stage-copy { min-width: 0; flex: 1; }
    .stage-state.waiting { color: rgba(109, 113, 123, 1); background: rgba(109, 113, 123, .1); }
    .mini-progress { height: 3px; margin-top: 5px; overflow: hidden; background: rgba(46, 121, 191, .15); border-radius: 2px; }
    .mini-progress span { display: block; height: 100%; background: rgba(35, 135, 235, 1); transition: width .3s ease; }

    .queue-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 5px; }
    .queue-cell { padding: 6px 3px; text-align: center; background: rgba(80, 87, 112, .055); border-radius: 4px; }
    .queue-cell b { display: block; font-size: 14px; }
    .queue-cell span { color: rgba(110, 113, 124, 1); font-size: 9px; }
    .queue-cell.running b { color: rgba(22, 110, 207, 1); }
    .queue-cell.succeeded b { color: rgba(27, 133, 90, 1); }
    .queue-cell.failed b { color: rgba(196, 69, 55, 1); }
    .worker-list { display: grid; gap: 5px; margin-top: 8px; }
    .worker-row { display: grid; grid-template-columns: 8px max-content minmax(0, 1fr); gap: 6px; align-items: center; color: rgba(74, 78, 90, 1); font-size: 10px; }
    .worker-query, .recent-query, .work-message { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .empty-line, .loading-line { padding: 7px 0; color: rgba(119, 123, 134, 1); font-size: 10px; }

    .active-work { background: linear-gradient(90deg, rgba(31, 136, 229, .06), transparent); }
    .work-row { display: grid; grid-template-columns: 68px minmax(0, 1fr); gap: 6px; margin-top: 6px; font-size: 10px; }
    .work-source { color: rgba(37, 104, 180, 1); font-weight: 700; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .work-message { color: rgba(78, 82, 94, 1); }
    .recent-row { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 6px; margin-top: 6px; align-items: center; }
    .task-status { color: rgba(99, 103, 113, 1); background: rgba(99, 103, 113, .1); }
    .task-status.succeeded { color: rgba(20, 115, 74, 1); background: rgba(42, 159, 109, .12); }
    .task-status.failed { color: rgba(171, 59, 48, 1); background: rgba(208, 68, 54, .12); }
    .task-status.running { color: rgba(12, 92, 180, 1); background: rgba(35, 135, 235, .12); }
    .recent-query { color: rgba(79, 83, 94, 1); font-size: 10px; }
    .panel-error { display: flex; gap: 7px; padding: 11px 0; color: rgba(174, 59, 49, 1); font-size: 11px; line-height: 16px; }

    @keyframes breathe { 50% { opacity: .45; } }
    @keyframes rotate { to { transform: rotate(360deg); } }
}

@media (max-width: 900px) {
    .web-pipeline-status { top: 82px; right: 10px; width: min(340px, calc(100% - 20px)); max-height: calc(100% - 320px); }
}
</style>
