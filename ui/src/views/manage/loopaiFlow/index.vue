<template>
    <div class="lp-workspace">
        <task-column></task-column>

        <section class="lp-workspace__main">
            <header class="lp-bar">
                <span class="lp-bar__title lp-truncate">
                    {{ currentTask ? currentTask.name : local('No task yet') }}
                </span>

                <span v-if="isRunning" class="lp-chip lp-chip--run">
                    <span class="lp-dot is-running"></span>
                    {{ runningLabel }}
                </span>
                <span v-else-if="currentTask" class="lp-chip">
                    <span class="lp-dot" :class="{ 'is-failed': hasFailed }"></span>
                    {{ hasFailed ? local('failed') : local('idle') }}
                </span>

                <div class="lp-bar__spacer"></div>

                <div class="lp-seg" role="tablist">
                    <button
                        v-for="option in viewOptions"
                        :key="option.key"
                        type="button"
                        role="tab"
                        class="lp-seg__item"
                        :class="{ 'is-active': view === option.key }"
                        :aria-selected="view === option.key"
                        @click="setView(option.key)"
                    >
                        {{ local(option.label) }}
                    </button>
                </div>

                <div class="lp-bar__sep"></div>

                <button
                    v-if="isRunning"
                    type="button"
                    class="lp-btn lp-btn--danger"
                    @click="handleStopClick"
                >
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor">
                        <rect x="6" y="6" width="12" height="12" rx="2" />
                    </svg>
                    {{ local('Stop') }}
                </button>

                <button
                    v-else-if="resetArmed"
                    type="button"
                    class="lp-btn lp-btn--danger"
                    @click="handleResetConfirm"
                >
                    {{ local('Confirm reset') }}
                </button>

                <button
                    v-else
                    type="button"
                    class="lp-btn lp-btn--ghost lp-btn--icon"
                    :disabled="!currentTask"
                    :title="local('Reset conversation')"
                    @click="armReset"
                >
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round">
                        <path d="M20 12a8 8 0 1 1-2.3-5.6" />
                        <path d="M20 4v4h-4" />
                    </svg>
                </button>

                <button
                    type="button"
                    class="lp-btn lp-btn--ghost lp-btn--icon"
                    :title="local('Resources')"
                    @click="show.dataset = true"
                >
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round">
                        <ellipse cx="12" cy="6" rx="7" ry="2.6" />
                        <path d="M5 6v12c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6V6" />
                        <path d="M5 12c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6" />
                    </svg>
                </button>

                <button
                    type="button"
                    class="lp-btn lp-btn--ghost lp-btn--icon"
                    :disabled="!currentTask"
                    :title="local('States')"
                    @click="handleAdjustStatesClick"
                >
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round">
                        <path d="M4 7h10M18 7h2M4 17h4M12 17h8" />
                        <circle cx="16" cy="7" r="2" />
                        <circle cx="10" cy="17" r="2" />
                    </svg>
                </button>
            </header>

            <div class="lp-workspace__body">
                <div v-show="view !== 'log'" class="lp-workspace__canvas">
                    <mainFlow
                        :id="flowId"
                        v-model:nodes="nodes"
                        v-model:edges="edges"
                        @show-node-detail="showDetailNode"
                    ></mainFlow>
                </div>
                <msg-list v-show="view !== 'graph'" :class="{ 'is-wide': view === 'log' }"></msg-list>
            </div>

            <footer class="lp-workspace__composer">
                <query-block></query-block>
            </footer>
        </section>

        <resourcePanel v-model="show.dataset" :title="local('Resources')"></resourcePanel>
        <task-state-panel v-model="show.statePanel"></task-state-panel>
        <detail-node-panel
            v-if="detailNodeProps"
            v-model="show.detailNode"
            :node-props="detailNodeProps"
        ></detail-node-panel>
    </div>
</template>

<script>
import { mapState, mapActions } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'
import { useTheme } from '@/stores/theme'
import { useVueFlow } from '@vue-flow/core'
import { useLoopAI } from '@/stores/loopAI'

import mainFlow from '@/components/manage/mainFlow/index.vue'
import taskColumn from '@/components/manage/mainFlow/tasks/index.vue'
import queryBlock from '@/components/manage/chat/queryBlock.vue'
import msgList from '@/components/manage/chat/msgList.vue'
import resourcePanel from '@/components/manage/mainFlow/panels/resourcePanel/index.vue'
import detailNodePanel from '@/components/manage/mainFlow/panels/detailNodePanel.vue'
import taskStatePanel from '@/components/manage/loopaiFlow/taskStatePanel.vue'

const VIEW_STORAGE_KEY = 'loopai-workspace-view'

/** Agent identity is a hue, at one chroma — never a gradient. */
const HUE = {
    looper: '#7fb2e8',
    obtainer: '#59b3a9',
    trainer: '#d4a24c',
    judger: '#6fbf87',
    analyzer: '#9d8ce8'
}

export default {
    name: 'LoopaiFlow',
    components: {
        mainFlow,
        taskColumn,
        queryBlock,
        msgList,
        resourcePanel,
        detailNodePanel,
        taskStatePanel
    },
    data() {
        return {
            flowId: 'lp-main-flow',
            view: 'split',
            viewOptions: [
                { key: 'graph', label: 'Graph' },
                { key: 'split', label: 'Split' },
                { key: 'log', label: 'Log' }
            ],
            resetArmed: false,
            resetArmTimer: null,
            nodes: [
                {
                    id: 'trainer',
                    type: 'agent-node',
                    position: { x: 230, y: 96 },
                    data: {
                        label: 'Trainer',
                        status: 'Agent',
                        stateKey: 'trainer',
                        graphClsPrefix: 'trainer',
                        include_nodes: ['trainer'],
                        icon: 'Library',
                        nodeInfo: 'Trains the model on the exported mix.',
                        iconColor: HUE.trainer,
                        borderColor: HUE.trainer
                    }
                },
                {
                    id: 'obtainer',
                    type: 'agent-node',
                    position: { x: 230, y: 637 },
                    data: {
                        label: 'Obtainer',
                        status: 'Agent',
                        stateKey: 'obtainer',
                        graphClsPrefix: 'obtainer',
                        include_nodes: ['obtainer', 'obtainercli'],
                        icon: 'GiftboxOpen',
                        nodeInfo: 'Acquires, processes and exports the training mix.',
                        iconColor: HUE.obtainer,
                        reverseHandle: true,
                        borderColor: HUE.obtainer
                    }
                },
                {
                    id: 'judger',
                    type: 'agent-node',
                    position: { x: 824, y: 95 },
                    data: {
                        label: 'Judger',
                        status: 'Agent',
                        stateKey: 'judger',
                        graphClsPrefix: 'judger',
                        include_nodes: ['judger'],
                        icon: 'Bug',
                        nodeInfo: 'Evaluates the checkpoint against the benchmarks.',
                        iconColor: HUE.judger,
                        borderColor: HUE.judger
                    }
                },
                {
                    id: 'analyzer',
                    type: 'agent-node',
                    position: { x: 1360, y: 800 },
                    data: {
                        label: 'Analyzer',
                        status: 'Agent',
                        stateKey: 'analyzer',
                        graphClsPrefix: 'analyzer',
                        include_nodes: ['analyzer'],
                        icon: 'AreaChart',
                        nodeInfo: 'Reads the results and decides what the next round needs.',
                        iconColor: HUE.analyzer,
                        reverseHandle: true,
                        borderColor: HUE.analyzer
                    }
                },
                {
                    id: 'looper',
                    type: 'agent-node',
                    position: { x: -250, y: 95 },
                    data: {
                        label: 'Looper',
                        status: 'Agent',
                        stateKey: 'looper',
                        graphClsPrefix: 'looper',
                        include_nodes: ['looper'],
                        icon: 'Robot',
                        nodeInfo: 'Orchestrates the round and hands off between agents.',
                        iconColor: HUE.looper,
                        reverseHandle: true,
                        borderColor: HUE.looper
                    }
                }
            ],
            edges: [
                { id: '0', type: 'base-edge', source: 'trainer', target: 'judger', animated: true },
                { id: '1', type: 'base-edge', source: 'judger', target: 'analyzer', animated: true },
                { id: '2', type: 'base-edge', source: 'analyzer', target: 'obtainer', animated: true },
                { id: '3', type: 'base-edge', source: 'obtainer', target: 'trainer', animated: true }
            ],
            detailNodeProps: null,
            timer: {
                healthCheck: null
            },
            show: {
                dataset: false,
                statePanel: false,
                detailNode: true
            }
        }
    },
    watch: {
        'taskStatus.running'(val) {
            if (val) this.getStatus(this.currentTask?.task_id)
        },
        currentTask(val) {
            this.clearLooperTakeoverCountdown()
            this.resetArmed = false
            if (val) this.getStatus(val.task_id)
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        ...mapState(useTheme, ['color']),
        ...mapState(useLoopAI, ['currentTask', 'taskStatus', 'taskMessages', 'msgStreamModel']),
        isRunning() {
            return this.msgStreamModel.loading
        },
        hasFailed() {
            return this.msgStreamModel.status === 'failed'
        },
        runningNode() {
            try {
                const running = this.taskStatus.node_status.find((node) => node.status === 'running')
                return running?.node_name || null
            } catch (error) {
                return null
            }
        },
        runningLabel() {
            return this.runningNode || this.local('running')
        }
    },
    mounted() {
        this.restoreView()
        this.fitGraph()
        this.resumeTask().then(() => this.getStatus(this.currentTask?.task_id))
        this.healthCheckInit()
        this.getStateSchema()
    },
    beforeUnmount() {
        clearInterval(this.timer.healthCheck)
        clearTimeout(this.resetArmTimer)
        this.clearLooperTakeoverCountdown()
    },
    methods: {
        ...mapActions(useLoopAI, [
            'getStatus',
            'getStateSchema',
            'resumeTask',
            'resetStarterCodexSession',
            'terminateStarterCodexSession',
            'clearLooperTakeoverCountdown'
        ]),
        restoreView() {
            try {
                const stored = localStorage.getItem(VIEW_STORAGE_KEY)
                if (this.viewOptions.some((option) => option.key === stored)) this.view = stored
            } catch (error) {
                /* private mode — the default split view is fine */
            }
        },
        setView(key) {
            this.view = key
            try {
                localStorage.setItem(VIEW_STORAGE_KEY, key)
            } catch (error) {
                /* nothing to persist to */
            }
        },
        /* Open on the whole loop rather than on whichever node happens to sit at 0,0. */
        fitGraph() {
            const flow = useVueFlow(this.flowId)
            flow.onNodesInitialized(() => {
                flow.fitView({ padding: 0.18, maxZoom: 1 })
            })
        },
        healthCheckInit() {
            clearInterval(this.timer.healthCheck)
            this.timer.healthCheck = setInterval(async () => {
                await this.getStatus(this.currentTask?.task_id)
            }, 5000)
        },
        handleAdjustStatesClick() {
            if (!this.currentTask?.task_id) return
            this.show.statePanel = true
        },
        /* Reset drops the conversation, so it arms in place instead of opening a dialog. */
        armReset() {
            if (!this.currentTask?.task_id) return
            this.resetArmed = true
            clearTimeout(this.resetArmTimer)
            this.resetArmTimer = setTimeout(() => {
                this.resetArmed = false
            }, 4000)
        },
        async handleResetConfirm() {
            this.resetArmed = false
            clearTimeout(this.resetArmTimer)
            this.clearLooperTakeoverCountdown()
            try {
                const res = await this.resetStarterCodexSession()
                if (res?.code !== 200) {
                    this.$barWarning(res?.message || this.local('Failed to reset conversation.'), {
                        status: 'warning'
                    })
                }
            } catch (error) {
                this.$barWarning(this.local('Failed to reset conversation.'), { status: 'error' })
            }
        },
        /* Stop is a stop, like ctrl-c: it acts on the first click. */
        async handleStopClick() {
            this.clearLooperTakeoverCountdown()
            if (!this.currentTask?.task_id) return
            try {
                const res = await this.terminateStarterCodexSession()
                if (res?.code !== 200) {
                    this.$barWarning(res?.message || this.local('Failed to terminated conversation.'), {
                        status: 'warning'
                    })
                }
            } catch (error) {
                this.$barWarning(this.local('Failed to terminated conversation.'), { status: 'error' })
            }
        },
        showDetailNode(props) {
            this.detailNodeProps = props
            this.show.detailNode = true
        }
    }
}
</script>

<style lang="scss">
.lp-workspace {
    position: relative;
    width: 100%;
    height: 100%;
    background: var(--lp-bg);
    display: flex;
    overflow: hidden;

    .lp-workspace__main {
        position: relative;
        flex: 1;
        min-width: 0;
        display: flex;
        flex-direction: column;
    }

    .lp-workspace__body {
        flex: 1;
        min-height: 0;
        display: flex;
    }

    .lp-workspace__canvas {
        position: relative;
        flex: 1;
        min-width: 0;
        background: var(--lp-bg);
        overflow: hidden;
    }

    .lp-workspace__composer {
        flex-shrink: 0;
        padding: 12px 16px 16px 16px;
        border-top: 1px solid var(--lp-line);
    }
}

@media (max-width: 1100px) {
    .lp-workspace .lp-tasks {
        display: none;
    }
}
</style>
