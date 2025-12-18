<template>
    <div class="lp-default-container" :class="[{ 'show-pipeline': show.pipeline }]">
        <task-nav v-model="show.taskNav" class="lp-task-container"></task-nav>
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
                </fv-command-bar>
            </div>
        </div>
        <page-loading :model-value="!lock.loading" title="Loading..."></page-loading>
    </div>
</template>

<script>
import { mapState, mapActions } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'
import { useTheme } from '@/stores/theme'
import { useVueFlow } from '@vue-flow/core'

import mainFlow from '@/components/manage/mainFlow/index.vue'
import taskNav from '@/components/manage/mainFlow/tasks/index.vue'
import pageLoading from '@/components/general/pageLoading.vue'

import databaseIcon from '@/assets/flow/database.svg'
import pipelineIcon from '@/assets/flow/pipeline.svg'
import saveIcon from '@/assets/flow/save.svg'

export default {
    components: {
        mainFlow,
        taskNav,
        pageLoading
    },
    data() {
        return {
            flowId: 'lp-main-flow',
            value: null,
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
                    id: '1',
                    type: 'base-node',
                    position: { x: 70, y: 160 },
                    data: {
                        label: 'Node 1',
                        nodeInfo:
                            'Node Info: This is node info block for displaying node information.',
                        iconColor: 'rgba(0, 108, 126, 1)'
                    }
                },
                {
                    id: '2',
                    type: 'base-node',
                    position: { x: 100, y: 400 },
                    data: {
                        label: 'Node 2',
                        nodeInfo:
                            'Node Info: This is node info block for displaying node information.',
                        icon: 'Accept'
                    }
                },
                {
                    id: '3',
                    type: 'base-node',
                    position: { x: 400, y: 800 },
                    data: { label: 'Node 3', icon: 'Cloud' }
                }
            ],

            edges: [
                {
                    id: 'e1->2',
                    type: 'base-edge',
                    source: '1',
                    target: '2'
                },
                {
                    id: 'e2->3',
                    type: 'base-edge',
                    source: '2',
                    target: '3',
                    animated: true,
                    data: {
                        label: 'world'
                    }
                }
            ],
            show: {
                taskNav: false
            },
            lock: {
                serving: true,
                running: true,
                loading: true
            }
        }
    },
    watch: {},
    computed: {
        ...mapState(useAppConfig, ['local']),
        ...mapState(useTheme, ['color', 'gradient'])
    },
    mounted() {
        this.setViewport()
    },
    methods: {
        setViewport() {
            const flow = useVueFlow(this.flowId)
            flow.setViewport({
                x: 0,
                y: 0,
                zoom: 1
            })
        },
        handleSaveClick() {}
    }
}
</script>

<style lang="scss">
.lp-default-container {
    position: relative;
    width: 100%;
    height: 100%;
    padding: 15px;
    background-color: rgba(241, 241, 241, 1);
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
        z-index: 2;
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
