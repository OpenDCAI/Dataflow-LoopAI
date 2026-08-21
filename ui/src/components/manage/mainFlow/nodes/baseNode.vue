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
        <div v-show="running" class="lp-flow-node-run"></div>
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
                    <p class="main-title" :title="thisData.label">{{ thisData.label }}</p>
                    <p class="sub-status" :title="thisData.status">{{ thisData.status }}</p>
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
            <div v-if="$slots.default" class="remain-content-block" :class="[{ row: rowLayoutContent }]">
                <slot></slot>
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
</script>

<style lang="scss">
/**
 * A node is a flat card with a hue rule on top. Running is a moving hairline,
 * not a halo; selection is the accent border, not a glow.
 */
.lp-flow-default-node {
    position: relative;
    width: auto;
    height: auto;
    --border-radius: var(--lp-r-3);

    .lp-flow-node-run {
        position: absolute;
        left: 0px;
        top: 0px;
        width: 100%;
        height: 2px;
        border-radius: 2px;
        overflow: hidden;
        z-index: 1;

        &::after {
            content: '';
            position: absolute;
            width: 40%;
            height: 100%;
            background: var(--lp-run);
            animation: lp-node-run 1.6s var(--lp-ease) infinite;
        }
    }

    @keyframes lp-node-run {
        0% {
            transform: translateX(-100%);
        }
        100% {
            transform: translateX(350%);
        }
    }

    .lp-flow-node-container {
        position: relative;
        width: 250px;
        height: auto;
        max-height: 460px;
        padding: 0px 0px 8px 0px;
        background: var(--lp-surface);
        border: 1px solid var(--lp-line);
        border-top: 2px solid var(--node-icon-color, var(--lp-line-hi));
        border-radius: var(--lp-r-3);
        display: flex;
        flex-direction: column;
        transition: border-color var(--lp-fast) var(--lp-ease);

        &.row-mode {
            width: auto;
            padding: 0px 0px 8px 0px;
        }

        &:hover {
            border-left-color: var(--lp-line-hi);
            border-right-color: var(--lp-line-hi);
            border-bottom-color: var(--lp-line-hi);
        }

        .node-banner {
            position: relative;
            width: calc(100% - 24px);
            height: auto;
            margin: 10px 12px 0px 12px;
            gap: 8px;
            display: flex;
            align-items: center;

            .icon-block {
                position: relative;
                width: 22px;
                height: 22px;
                flex-shrink: 0;
                background: transparent !important;
                border: 1px solid var(--lp-line-hi);
                border-radius: var(--lp-r-1);
                color: var(--node-icon-color, var(--lp-text-dim));
                font-size: 11px;
                display: flex;
                justify-content: center;
                align-items: center;

                .icon-img {
                    width: auto;
                    height: 13px;
                }
            }

            .content-block {
                position: relative;
                width: 50px;
                flex: 1;
                display: flex;
                flex-direction: column;
                justify-content: center;

                .main-title {
                    width: 100%;
                    font-size: var(--lp-t-md);
                    font-weight: 600;
                    color: var(--lp-text);
                    text-overflow: ellipsis;
                    white-space: nowrap;
                    overflow: hidden;
                }

                .sub-status {
                    width: 100%;
                    font-family: var(--lp-mono);
                    font-size: var(--lp-t-xs);
                    color: var(--lp-text-mute);
                    text-overflow: ellipsis;
                    white-space: nowrap;
                    overflow: hidden;
                }
            }

            .control-block {
                position: relative;
                width: auto;
                gap: 4px;
                flex-shrink: 0;
                display: flex;
                align-items: center;
            }
        }

        .node-info {
            position: relative;
            width: calc(100% - 24px);
            max-width: 220px;
            margin: 6px 12px 0px 12px;
            flex-shrink: 0;
            font-size: 11px;
            line-height: 1.5;
            color: var(--lp-text-mute);
            overflow: hidden;
        }

        .remain-content-block {
            position: relative;
            width: 100%;
            height: auto;
            margin-top: 10px;
            gap: 0px;
            flex: 1;
            background: transparent;
            display: flex;
            flex-direction: column;
            overflow: auto;

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
                    border-left: 1px solid var(--lp-line);
                }
            }

            .node-group-item {
                position: relative;
                width: 100%;
                height: auto;
                padding: 0px;
                font-weight: 400;
                line-height: 1.6;
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
                padding: 4px 12px;
                gap: 10px;
                display: flex;
                justify-content: space-between;

                &.col {
                    gap: 4px;
                    flex-direction: column;
                    justify-content: flex-start;
                    align-items: flex-start;
                }

                &.w-pad {
                    padding: 4px 12px;
                }

                .info-value {
                    margin-left: 0px;
                    text-overflow: ellipsis;
                    text-align: right;
                }
            }

            hr {
                width: calc(100% - 24px);
                margin-left: 12px;
                border: 0px;
                border-top: 1px solid var(--lp-line);
            }

            .info-title {
                font-family: var(--lp-mono);
                font-size: var(--lp-t-xs);
                font-weight: 400;
                color: var(--lp-text-mute);
                display: flex;
                align-items: center;
            }

            .info-value {
                flex: 1;
                min-width: 0;
                font-family: var(--lp-mono);
                font-size: var(--lp-t-sm);
                font-weight: 400;
                color: var(--lp-text-dim);
                overflow: hidden;

                &.tiny {
                    font-size: var(--lp-t-xs);
                }
            }
        }

        .handle-item {
            width: 8px;
            height: 8px;
            background: var(--lp-line-hi);
            border: 2px solid var(--lp-bg);
            transition: background var(--lp-fast) var(--lp-ease);

            &:hover {
                background: var(--lp-accent);
            }

            &.default {
                background: var(--node-border-color, var(--lp-line-hi));
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
                        padding: 4px 6px;
                        font-family: var(--lp-mono);
                        font-size: var(--lp-t-sm);
                        color: var(--lp-text);
                        background: var(--lp-surface);
                        border: 1px solid var(--lp-line-hi);
                        border-radius: var(--lp-r-1);
                        white-space: nowrap;
                        transform: translate(calc(-100% - 10px), -50%);
                    }
                }
            }
        }
    }

    &.selected .lp-flow-node-container {
        border-left-color: var(--lp-accent);
        border-right-color: var(--lp-accent);
        border-bottom-color: var(--lp-accent);
    }
}
</style>
