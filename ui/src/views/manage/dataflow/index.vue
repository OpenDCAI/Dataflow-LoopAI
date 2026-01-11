<template>
    <div class="lp-default-container" :class="[{ 'show-pipeline': show.pipeline }]">
        <page-loading :model-value="!currentTask || !currentTask.task_id" :z-index="3" acrylic>
            <h1>{{ local('Start with a new Task') }}</h1>
            <fv-button
                theme="dark"
                icon="OpenPaneMirrored"
                :background="gradient"
                border-radius="12"
                font-size="16"
                style="width: 150px; height: 45px; margin-top: 25px"
                @click="show.taskNav = true"
                >{{ local('Open Tasks') }}</fv-button
            >
        </page-loading>
        <task-nav
            v-model="show.taskNav"
            class="lp-task-container"
            v-model:task="currentTask"
        ></task-nav>
        <div class="lp-flow-container">
            <mainFlow :id="flowId" v-model:nodes="nodes" v-model:edges="edges"></mainFlow>
            <div class="control-menu-block">
                <fv-command-bar
                    v-model="value"
                    :options="options"
                    :item-border-radius="30"
                    background="rgba(250, 250, 250, 0.8)"
                    class="command-bar"
                >
                    <template v-slot:optionItem="x">
                        <div class="command-bar-item-wrapper">
                            <fv-img v-if="x.item.img" class="option-img" :src="x.item.img" alt="" />
                            <i
                                v-else
                                class="ms-Icon icon"
                                :class="[`ms-Icon--${x.valueTrigger(x.item.icon)}`]"
                                :style="{ color: x.valueTrigger(x.item.foreground) }"
                            ></i>
                            <p
                                class="option-name"
                                :style="{ color: x.valueTrigger(x.item.foreground) }"
                            >
                                {{ x.valueTrigger(x.item.name) }}
                            </p>
                            <i
                                v-show="x.item.secondary.length > 0"
                                class="ms-Icon ms-Icon--ChevronDown icon"
                            ></i>
                        </div>
                    </template>
                    <template v-slot:right-space>
                        <div class="command-bar-right-space">
                            <fv-button
                                theme="dark"
                                background="linear-gradient(
                                    90deg,
                                    rgba(129, 208, 246, 1),
                                    rgba(146, 156, 218, 1)
                                )"
                                foreground="rgba(255, 255, 255, 1)"
                                border-color="rgba(255, 255, 255, 0.3)"
                                border-radius="30"
                                :disabled="(!currentTask || !currentTask.task_id) && !isRunning"
                                :reveal-background-color="[
                                    'rgba(255, 255, 255, 0.5)',
                                    'rgba(103, 105, 251, 0.6)'
                                ]"
                                @click="handleExecute"
                            >
                                <i
                                    class="ms-Icon"
                                    :class="[`ms-Icon--${isRunning ? 'CheckboxFill' : 'Play'}`]"
                                    style="margin-right: 5px"
                                ></i>
                                <fv-progress-ring
                                    v-show="!lock.loading"
                                    loading="true"
                                    :r="10"
                                    :border-width="2"
                                    background="rgba(200, 200, 200, 1)"
                                    :color="'white'"
                                    style="margin-right: 5px"
                                ></fv-progress-ring>
                                <p>{{ isRunning ? this.local('Stop') : this.local('Run') }}</p>
                            </fv-button>
                            <i
                                class="ms-Icon ms-Icon--FullCircleMask status-coin"
                                :class="[
                                    { ready: taskStatus.running && !taskStatus.waiting_llm },
                                    { running: taskStatus.running && taskStatus.waiting_llm }
                                ]"
                                style="margin-left: 5px"
                            ></i>
                        </div>
                    </template>
                </fv-command-bar>
            </div>
            <div class="chat-query-block" :class="[{ 'full-screen': show.fullScreen }]">
                <query-block v-model:full-screen-editor="show.fullScreen"></query-block>
            </div>
            <msg-list></msg-list>
        </div>
        <page-loading :model-value="!lock.loading" title="Loading..."></page-loading>
    </div>
</template>

<script>
import { mapState, mapActions } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'
import { useTheme } from '@/stores/theme'
import { useVueFlow } from '@vue-flow/core'
import { useLoopAI } from '@/stores/loopAI'

import mainFlow from '@/components/manage/mainFlow/index.vue'
import taskNav from '@/components/manage/mainFlow/tasks/index.vue'
import pageLoading from '@/components/general/pageLoading.vue'
import queryBlock from '@/components/manage/chat/queryBlock.vue'
import msgList from '@/components/manage/chat/msgList.vue'

import databaseIcon from '@/assets/flow/database.svg'
import pipelineIcon from '@/assets/flow/pipeline.svg'
import saveIcon from '@/assets/flow/save.svg'

export default {
    components: {
        mainFlow,
        taskNav,
        pageLoading,
        queryBlock,
        msgList
    },
    data() {
        return {
            flowId: 'lp-main-flow',
            value: null,
            currentTask: null,
            options: [
                {
                    name: () => this.local('Dataset'),
                    icon: 'Play',
                    img: databaseIcon,
                    func: () => {
                        this.show.dataset = true
                    }
                },
                {
                    name: () => this.local('Task'),
                    img: pipelineIcon,
                    func: () => {
                        this.show.taskNav ^= true
                    }
                },
                {
                    name: () => this.local('Save'),
                    img: saveIcon,
                    func: () => {
                        this.handleSaveClick()
                    }
                }
            ],
            nodes: [
                {
                    id: 'configer',
                    type: 'agent-node',
                    position: { x: 0, y: 0 },
                    data: {
                        label: 'Configer',
                        status: 'Agent',
                        stateKey: 'configer',
                        graphClsPrefix: 'ConfigerAgent',
                        icon: 'Settings',
                        nodeInfo: 'Trainer Agent for Training',
                        iconColor: 'rgba(149, 91, 120, 1)',
                        background:
                            'linear-gradient(130deg, rgba(201, 122, 162, 0.8), rgba(252, 252, 252, 0.8))',
                        borderColor: 'rgba(201, 122, 162, 0.8)'
                    }
                },
                {
                    id: 'trainer',
                    type: 'agent-node',
                    position: { x: 350, y: 160 },
                    data: {
                        label: 'Trainer',
                        status: 'Agent',
                        icon: 'Library',
                        nodeInfo: 'Trainer Agent for Training',
                        iconColor: 'rgba(207, 150, 12, 1)',
                        background:
                            'linear-gradient(130deg, rgba(239, 192, 40, 0.8), rgba(252, 252, 252, 0.8))',
                        borderColor: 'rgba(239, 192, 40, 0.8)'
                    }
                },
                {
                    id: 'obtainer',
                    type: 'agent-node',
                    position: { x: 80, y: 801 },
                    data: {
                        label: 'Obtainer',
                        status: 'Agent',
                        stateKey: 'obtainer',
                        graphClsPrefix: 'ObtainerAgent',
                        icon: 'GiftboxOpen',
                        nodeInfo: 'Trainer Agent for Training',
                        iconColor: 'rgba(135, 127, 163, 1)',
                        reverseHandle: true
                    }
                },
                {
                    id: 'judger',
                    type: 'agent-node',
                    position: { x: 700, y: 161 },
                    data: {
                        label: 'Judger',
                        status: 'Agent',
                        stateKey: 'judger',
                        graphClsPrefix: 'JudgerAgent',
                        icon: 'Bug',
                        nodeInfo: 'Trainer Agent for Training',
                        iconColor: 'rgba(89, 169, 133, 1)',
                        background:
                            'linear-gradient(130deg, rgba(116, 220, 175, 0.8), rgba(252, 252, 252, 0.8))',
                        borderColor: 'rgba(116, 220, 175, 0.8)'
                    }
                },
                {
                    id: 'analyzer',
                    type: 'agent-node',
                    position: { x: 910, y: 800 },
                    data: {
                        label: 'Analyzer',
                        status: 'Agent',
                        stateKey: 'analyzer',
                        graphClsPrefix: 'AnalyzerAgent',
                        icon: 'AreaChart',
                        nodeInfo: 'Trainer Agent for Training',
                        iconColor: 'rgba(139, 145, 177, 1)',
                        background:
                            'linear-gradient(130deg, rgba(150, 167, 222, 0.8), rgba(252, 252, 252, 0.8))',
                        reverseHandle: true,
                        borderColor: 'rgba(150, 167, 222, 0.8)'
                    }
                }
            ],

            edges: [
                {
                    id: 'e1->2',
                    type: 'base-edge',
                    source: 'obtainer',
                    target: 'trainer'
                },
                {
                    id: 'e2->3',
                    type: 'base-edge',
                    source: 'trainer',
                    target: 'judger',
                    animated: true,
                    data: {
                        label: 'node'
                    }
                },
                {
                    id: 'e3->4',
                    type: 'base-edge',
                    source: 'judger',
                    target: 'analyzer',
                    animated: true,
                    data: {
                        label: 'node'
                    }
                },
                {
                    id: 'e4->5',
                    type: 'base-edge',
                    source: 'analyzer',
                    target: 'obtainer',
                    animated: true,
                    data: {
                        label: 'node'
                    }
                }
            ],
            timer: {
                healthCheck: null
            },
            show: {
                taskNav: false,
                fullScreen: false
            },
            lock: {
                loading: true
            }
        }
    },
    watch: {
        'taskStatus.running'(val) {
            if (val) this.getStatus()
        },
        'currentTask.task_id'() {
            this.stop()
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        ...mapState(useTheme, ['color', 'gradient']),
        ...mapState(useLoopAI, ['taskStatus', 'taskMessages', 'msgStreamModel']),
        isRunning() {
            return this.taskStatus.running
        }
    },
    mounted() {
        this.setViewport()
        this.getStatus()
        this.healthCheckInit()
        this.getStateSchema()
    },
    methods: {
        ...mapActions(useLoopAI, ['getStatus', 'getMsgStream', 'getStateSchema']),
        setViewport() {
            const flow = useVueFlow(this.flowId)
            flow.setViewport({
                x: 0,
                y: 0,
                zoom: 1
            })
        },
        healthCheckInit() {
            clearInterval(this.timer.healthCheck)
            this.timer.healthCheck = setInterval(() => {
                this.getStatus()
            }, 5000)
        },
        handleSaveClick() {},
        handleExecute() {
            if (this.isRunning) this.stop()
            else this.execute()
        },
        stop() {
            this.$api.starter.stopAgent().then((res) => {
                if (res.code === 200) {
                    this.$barWarning('Stop signal sent', {
                        status: 'correct'
                    })
                }
            })
        },
        execute() {
            if (!this.currentTask || !this.currentTask.task_id) return
            if (!this.lock.loading) return
            this.lock.loading = false
            this.$api.starter
                .startAgent(this.currentTask.task_id)
                .then(async (res) => {
                    if (res.code === 200) {
                        await this.getStatus()
                        this.healthCheckInit()
                        this.lock.loading = true
                    } else {
                        this.lock.loading = true
                        this.$barWarning(res.message, {
                            status: 'warning'
                        })
                    }
                })
                .catch((error) => {
                    this.lock.loading = true
                    this.$barWarning(error.message, {
                        status: 'error'
                    })
                })
        }
    },
    beforeUnmount() {
        clearInterval(this.timer.healthCheck)
    }
}
</script>

<style lang="scss">
.lp-default-container {
    position: relative;
    width: 100%;
    height: 100%;
    padding: 15px;
    background-color: rgba(243, 243, 243, 1);
    display: flex;

    .lp-task-container {
        position: absolute;
        left: 0px;
        top: 15px;
        width: 300px;
        height: calc(100% - 30px);
        border-top-left-radius: 15px;
        border-bottom-left-radius: 15px;
        box-shadow: 1px 0px 2px rgba(120, 120, 120, 0.1);
        z-index: 3;
    }

    .lp-flow-container {
        position: relative;
        width: 100%;
        height: 100%;
        flex: 1;
        background: rgba(250, 250, 250, 1);
        border: rgba(120, 120, 120, 0.1) solid thin;
        border-radius: 15px;
        box-shadow: inset 0px 0px 6px rgba(0, 0, 0, 0.1);
        overflow: hidden;

        .chat-query-block {
            position: absolute;
            left: 0px;
            bottom: 0px;
            width: 100%;
            height: 250px;
            padding: 15px;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-direction: column;
            box-sizing: border-box;
            transition: all 0.3s;
            z-index: 2;

            &.full-screen {
                height: 600px;
                max-height: 100%;
            }
        }
    }

    .control-menu-block {
        position: absolute;
        left: 0px;
        top: 0px;
        width: 100%;
        height: auto;
        padding: 35px 0px;
        display: flex;
        justify-content: center;
        z-index: 2;

        .command-bar {
            min-width: 320px;
            width: 70%;
            max-width: 700px;
            border: rgba(120, 120, 120, 0.1) solid thin;
            border-radius: 30px;
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
            box-shadow: 0px 1px 2px rgba(0, 0, 0, 0.1);

            .command-bar-item {
                &:hover {
                    .option-img {
                        filter: grayscale(0);
                    }
                }
            }

            .command-bar-item-wrapper {
                position: relative;
                width: 100%;
                height: 100%;
                display: flex;
                align-items: center;

                &:hover {
                    .option-img {
                        filter: grayscale(0);
                    }
                }
            }

            .option-img {
                width: auto;
                height: 15px;
                filter: grayscale(100%);
                transition: filter 0.2s;
            }

            .option-name {
                margin-left: 8px;
                font-size: 12px;
                color: rgba(45, 48, 56, 1);
            }

            .command-bar-right-space {
                @include Vcenter;

                position: relative;
                width: auto;
                height: 100%;
                padding-right: 5px;
                gap: 3px;

                .status-coin {
                    font-size: 10px;
                    color: rgba(220, 38, 45, 1);
                    animation-direction: alternate;
                    transition: color 0.3s;

                    &.running {
                        color: rgba(255, 165, 0, 1);
                        animation: coin-running 0.5s linear infinite;
                    }

                    &.ready {
                        color: rgba(45, 168, 83, 1);
                    }

                    @keyframes coin-running {
                        0% {
                            transform: scale(1);
                        }
                        100% {
                            transform: scale(1.2);
                        }
                    }
                }
            }
        }
    }
}

.serving-menu {
    .serving-item {
        position: relative;
        flex-direction: column;
        line-height: 1.5;

        &.choosen {
            &::before {
                content: '';
                position: absolute;
                left: 0px;
                top: 10px;
                width: 3px;
                height: calc(100% - 20px);
                background: linear-gradient(135deg, rgba(69, 98, 213, 1), #ff0080);
                border-radius: 8px;
            }

            .main-title {
                @include color-rainbow;

                color: rgba(69, 98, 213, 1);
            }
        }

        .main-title {
            font-size: 16px;
        }

        .sec-title {
            font-size: 10px;
            color: rgba(120, 120, 120, 1);
        }
    }
}

.lp-scale-up-to-up-enter-active {
    animation: scaleUp 0.7s ease both;
    animation-delay: 0.3s;
}
.lp-scale-up-to-up-leave-active {
    position: absolute;
    width: 100%;
    height: 100%;
    display: flex;
    justify-content: center;
    align-items: center;
    animation: scaleDownUp 0.7s ease both;
    z-index: 8;
}
@keyframes scaleUp {
    from {
        opacity: 0;
        transform: scale(0.3);
    }
}
@keyframes scaleDownUp {
    to {
        opacity: 0;
        transform: scale(1.2);
    }
}
</style>
