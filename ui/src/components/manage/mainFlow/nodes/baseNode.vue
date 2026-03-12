<template>
    <div
        class="lp-flow-default-node"
        :class="[{ selected: selected }]"
        :style="{
            '--node-background': thisData.background,
            '--node-icon-color': thisData.iconColor,
            '--node-border-color': thisData.borderColor,
            '--node-shadow-color': thisData.shadowColor,
            '--node-group-background': thisData.groupBackground,
            '--node-title-color': thisData.titleColor,
            '--node-status-color': thisData.statusColor,
            '--node-info-title-color': thisData.infoTitleColor,
            '--default-handle-color': thisData.defaultHandleColor,
            '--default-handle-shadow-color': thisData.defaultHandleShadowColor
        }"
    >
        <div class="lp-flow-node-shadow" :class="[{ 'running-shadow': running }]"></div>
        <div class="lp-flow-node-container" :class="[{ 'row-mode': rowLayoutContent }]">
            <div class="node-banner" :title="id">
                <div class="icon-block" :style="{ background: thisData.iconBackground }">
                    <i
                        v-if="!thisData.img"
                        class="ms-Icon"
                        :class="[`ms-Icon--${thisData.icon}`]"
                    ></i>
                    <fv-img v-else class="icon-img" :src="thisData.img"></fv-img>
                </div>
                <div class="content-block">
                    <p class="sub-status" :title="thisData.status">{{ thisData.status }}</p>
                    <p class="main-title" :title="thisData.label">{{ thisData.label }}</p>
                </div>
                <div class="control-block" @mousedown.stop @click.stop>
                    <fv-button
                        v-if="thisData.enableDetail"
                        border-radius="8"
                        :font-size="12"
                        :is-box-shadow="true"
                        style="width: 25px; height: 25px; cursor: pointer"
                        @mousedown.stop
                        @click.stop
                        @click="$emit('show-node-detail', $props)"
                    >
                        <i class="ms-Icon ms-Icon--View"></i>
                    </fv-button>
                    <fv-button
                        v-if="thisData.enableDelete"
                        theme="dark"
                        border-radius="8"
                        :font-size="12"
                        background="rgba(215, 95, 95, 1)"
                        border-color="rgba(255, 255, 255, 0.1)"
                        style="width: 25px; height: 25px"
                        @click="
                            $emit('delete-node', {
                                id: id,
                                data: thisData
                            })
                        "
                    >
                        <i class="ms-Icon ms-Icon--Cancel"></i>
                    </fv-button>
                </div>
            </div>
            <div class="node-info">
                <p>{{ thisData.nodeInfo }}</p>
            </div>
            <div class="remain-content-block" :class="[{ row: rowLayoutContent }]">
                <slot>
                    <div class="node-group-item">
                        <div class="node-row-item">
                            <span class="info-title">Selected Info</span>
                            <p class="info-value">{{ selected }}</p>
                        </div>
                        <span class="info-title">Position Info</span>
                        <p class="info-value">{{ x }} {{ y }}</p>
                    </div>
                </slot>
            </div>
            <Handle
                v-if="thisData.useTargetHandle"
                :id="`node::target::node`"
                type="target"
                class="handle-item default"
                :position="!thisData.reverseHandle ? Position.Left : Position.Right"
                :style="{
                    top: thisData.defaultTargetTop
                }"
            />
            <Handle
                v-if="thisData.useSourceHandle"
                :id="`node::source::node`"
                type="source"
                class="handle-item default"
                :position="!thisData.reverseHandle ? Position.Right : Position.Left"
                :style="{
                    top: thisData.defaultSourceHandleTop
                }"
            />
        </div>
    </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Position, Handle } from '@vue-flow/core'

const props = defineProps({
    id: {
        type: String,
        required: true
    },
    position: {
        type: Object,
        required: true
    },
    selected: {
        type: Boolean,
        default: false
    },
    data: {
        type: Object,
        default: () => ({})
    },
    running: {
        type: Boolean,
        default: false
    },
    rowLayoutContent: {
        type: Boolean,
        default: false
    }
})

const defaultData = {
    label: 'Node',
    status: 'Status',
    nodeInfo: '',
    icon: 'EndPoint',
    iconColor: '',
    iconBackground: '',
    background: '',
    titleColor: '',
    statusColor: '',
    infoTitleColor: '',
    borderColor: '',
    shadowColor: '',
    defaultHandleColor: '',
    defaultHandleShadowColor: '',
    groupBackground: '',
    enableDelete: true,
    enableDetail: true,
    defaultSourceHandleTop: '',
    defaultTargetHandleTop: '',
    useSourceHandle: true,
    useTargetHandle: true,
    reverseHandle: false
}
const thisData = computed(() => {
    return {
        ...defaultData,
        ...props.data
    }
})
const x = computed(() => `${Math.round(props.position.x)}px`)
const y = computed(() => `${Math.round(props.position.y)}px`)
</script>

<style lang="scss">
.lp-flow-default-node {
    --border-radius: 12px;
    --node-background: rgba(252, 252, 252, 0.8);
    --node-title-color: rgba(100, 108, 126, 1);
    --node-status-color: rgba(168, 170, 176, 1);
    --node-info-title-color: rgba(168, 170, 176, 1);
    --node-shadow-color: rgba(122, 124, 206, 0.3);
    --node-border-color: rgba(163, 164, 236, 1);
    --node-icon-color: rgba(100, 108, 126, 1);
    --node-group-background: rgba(246, 246, 246, 0.8);
    --default-handle-color: rgba(163, 164, 236, 1);
    --default-handle-shadow-color: rgba(122, 124, 206, 0.3);

    border-radius: var(--border-radius);
    outline: 2px solid transparent;
    transition: outline 0.2s ease-in-out;

    &.selected {
        outline-color: var(--node-border-color);
    }

    .lp-flow-node-container {
        position: relative;
        width: 250px;
        height: 460px;
        padding: 5px 0px;
        background: rgba(255, 255, 255, 0.9);
        border: 1px solid rgba(120, 120, 120, 0.2);
        border-radius: var(--border-radius);
        outline: 2px solid transparent;
        display: flex;
        flex-direction: column;
        transition:
            box-shadow 0.2s ease-in-out,
            outline 0.2s ease-in-out;
        backdrop-filter: blur(6px);
        -webkit-backdrop-filter: blur(6px);
        overflow: hidden;
        box-shadow:
            0px 0px 1px var(--node-shadow-color),
            3px 6px 16px transparent,
            -3px 6px 16px transparent;

        &.row-mode {
            width: auto;
            padding: 10px;
        }

        &:hover {
            box-shadow:
                0px 0px 1px var(--node-shadow-color),
                3px 6px 16px var(--node-shadow-color),
                -3px 6px 16px var(--node-shadow-color);
        }

        &:active {
            box-shadow:
                0px 0px 1px var(--node-shadow-color),
                2px 3px 8px var(--node-shadow-color),
                -2px 3px 8px var(--node-shadow-color);
        }

        &.selected {
            outline-color: var(--node-border-color);
        }

        .node-banner {
            position: relative;
            width: calc(100% - 20px);
            height: auto;
            margin-left: 10px;
            display: flex;
            flex-direction: row;

            .icon-block {
                position: relative;
                width: 35px;
                height: 35px;
                background: var(--node-icon-color);
                border: 1px solid rgba(120, 120, 120, 0.1);
                border-radius: 8px;
                color: rgba(253, 253, 253, 1);
                display: flex;
                flex-direction: row;
                justify-content: center;
                align-items: center;
                box-shadow: 0px 1px 2px rgba(0, 0, 0, 0.1);

                &:hover {
                    background: rgba(255, 255, 255, 1);
                }

                &:active {
                    background: rgba(252, 252, 252, 1);
                }

                .icon-img {
                    width: auto;
                    height: 20px;
                }
            }

            .content-block {
                position: relative;
                width: calc(100% - 80px);
                height: 40px;
                flex: 1;
                padding-left: 5px;
                display: flex;
                flex-direction: column;
                justify-content: center;
                align-items: center;

                .main-title {
                    position: relative;
                    width: 100%;
                    height: auto;
                    font-size: 13.8px;
                    font-weight: 600;
                    color: var(--node-title-color);
                    text-overflow: ellipsis;
                    white-space: nowrap;
                    overflow: hidden;
                }

                .sub-status {
                    position: relative;
                    width: 100%;
                    height: auto;
                    font-size: 10px;
                    font-weight: 400;
                    color: var(--node-status-color);
                    text-overflow: ellipsis;
                    white-space: nowrap;
                    overflow: hidden;
                }
            }

            .control-block {
                position: relative;
                width: auto;
                height: 40px;
                padding: 0px;
                display: flex;
                flex-direction: row;
                justify-content: center;
                align-items: center;
            }
        }

        .node-info {
            position: relative;
            width: calc(100% - 20px);
            max-width: 170px;
            height: auto;
            margin: 3px 10px;
            font-size: 8px;
            font-weight: 400;
            color: var(--node-status-color);
            overflow: hidden;
        }

        .remain-content-block {
            position: relative;
            width: 100%;
            height: auto;
            margin-top: 5px;
            gap: 5px;
            background: var(--node-group-background);
            border-radius: var(--border-radius);
            font-family:
                system-ui,
                -apple-system,
                BlinkMacSystemFont,
                'Segoe UI',
                Roboto,
                Oxygen,
                Ubuntu,
                Cantarell,
                'Open Sans',
                'Helvetica Neue',
                sans-serif;
            display: flex;
            flex-direction: column;
            box-shadow: inset 0px 1px 3px rgba(120, 120, 120, 0.1);
            overflow: overlay;

            &.row {
                flex-direction: row;
            }

            .col-wrapper {
                position: relative;
                width: 100%;
                height: auto;
                display: flex;
                flex-direction: column;

                &:nth-child(2) {
                    background: rgba(245, 245, 245, 1);
                    border: 1px solid rgba(235, 235, 235, 1);
                    border-top: none;
                    border-bottom: none;
                }
            }

            .node-group-item {
                position: relative;
                width: 100%;
                height: auto;
                margin-bottom: 5px;
                padding: 5px;
                font-weight: 400;
                border-radius: 6px;
                line-height: 1.5;
                overflow-x: hidden;

                &.no-pad {
                    padding: 0px;
                }

                .node-row-item {
                    width: 100%;
                    margin-left: 0px;
                    padding: 0px;
                }
            }

            .node-row-item {
                position: relative;
                width: 100%;
                height: auto;
                padding: 5px 15px;
                display: flex;
                justify-content: space-between;

                &.col {
                    gap: 5px;
                    flex-direction: column;
                    justify-content: flex-start;
                    align-items: flex-start;
                }

                .info-value {
                    margin-left: 5px;
                    text-overflow: ellipsis;
                    text-align: right;
                }
            }

            hr {
                width: calc(100% - 20px);
                margin-left: 10px;
                border: 0px;
                border-top: 1px solid rgba(120, 120, 120, 0.1);
            }

            .info-title {
                font-size: 8px;
                font-weight: 600;
                color: var(--node-info-title-color);
                display: flex;
                align-items: center;
            }

            .info-value {
                flex: 1;
                font-size: 12px;
                font-weight: 400;
                color: var(--node-title-color);
                overflow: hidden;

                &.tiny {
                    font-size: 10px;
                }
            }
        }

        .handle-item {
            width: 15px;
            height: 15px;
            background: var(--node-border-color);
            outline: 3px solid transparent;
            border: rgba(250, 250, 250, 1) solid 3px;
            transition: all 0.2s ease-in-out;

            &:hover {
                width: 18px;
                height: 18px;
                outline: 3px solid var(--node-shadow-color);
            }

            &.default {
                background: var(--default-handle-color);

                &:hover {
                    outline: 3px solid var(--default-handle-shadow-color);
                }
            }

            &.title {
                &:hover {
                    &::before {
                        content: attr(data-title);
                        position: absolute;
                        top: 50%;
                        left: 0;
                        width: auto;
                        height: auto;
                        padding: 5px;
                        font-size: 12px;
                        background: rgba(250, 250, 250, 1);
                        border: 1px solid rgba(120, 120, 120, 0.1);
                        border-radius: 6px;
                        white-space: nowrap;
                        transform: translate(calc(-100% - 10px), -50%);
                    }
                }
            }

            &:active {
                width: 16px;
                height: 16px;
            }
        }
    }

    .lp-flow-node-shadow {
        position: absolute;
        width: 100%;
        height: 100%;
        z-index: -1;

        &.running-shadow {
            &:before,
            &:after {
                content: '';
                position: absolute;
                top: -2px;
                left: -2px;
                width: calc(100% + 4px);
                height: calc(100% + 4px);
                background: linear-gradient(
                    45deg,
                    rgba(226, 121, 162, 1),
                    rgba(146, 156, 218, 1),
                    rgba(129, 208, 246, 1),
                    rgba(239, 192, 48, 1),
                    rgba(246, 100, 100, 1),
                    rgba(226, 121, 162, 1),
                    rgba(146, 156, 218, 1),
                    rgba(129, 208, 246, 1),
                    rgba(239, 192, 48, 1),
                    rgba(246, 100, 100, 1)
                );
                background-size: 400%;
                border-radius: var(--border-radius);
                z-index: -1;
                animation: shadow 20s linear infinite;
            }

            &:after {
                top: -8px;
                left: -8px;
                width: calc(100% + 16px);
                height: calc(100% + 16px);
                filter: blur(24px);
                opacity: 0.9;
            }
        }

        @keyframes shadow {
            0% {
                background-position: 0 0;
            }

            50.01% {
                background-position: 200% 0;
            }

            100% {
                background-position: 0 0;
            }
        }
    }
}
</style>
