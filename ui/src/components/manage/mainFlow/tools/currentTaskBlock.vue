<template>
    <div
        v-if="modelValue"
        class="df-current-task-container"
        :class="[{ dark: theme === 'dark' }]"
        @mouseenter="inside = true"
        @mouseleave="inside = false"
    >
        <div class="row-item">
            <div class="df-current-task-title" :title="local('Current Task')">
                {{ modelValue.name }}
            </div>
            <transition name="df-cp-scale-up-to-up">
                <time-rounder
                    v-if="modelValue"
                    v-show="inside"
                    :model-value="new Date(modelValue.updatedAt)"
                    :foreground="color"
                    style="width: auto"
                ></time-rounder>
            </transition>
        </div>
        <div v-if="modelValue.task_id" class="row-item">
            <p class="df-current-sec-info" @click="copyText(modelValue.task_id)">
                {{ local('Task ID') }}: {{ modelValue.task_id }}
            </p>
        </div>
        <div v-if="modelValue.task_id && isRunning" class="row-item">
            <div class="board-item-block">
                <div
                    v-if="usageInfo.gpu_usage && usageInfo.gpu_usage.length > 0"
                    class="board-unit"
                    v-for="(gpu_value, gpu_index) in usageInfo.gpu_usage"
                    :key="gpu_value.gpu"
                >
                    <fv-progress-ring
                        :model-value="gpu_value"
                        r="25"
                        :border-width="5"
                        background="whitesmoke"
                        :color="color"
                    ></fv-progress-ring>
                    <p class="info-value">{{ gpu_value }}%</p>
                    <p class="info-name">GPU {{ gpu_index }}</p>
                </div>
                <div v-if="usageInfo.cpu_usage && usageInfo.cpu_usage.total" class="board-unit">
                    <fv-progress-ring
                        :model-value="usageInfo.cpu_usage.total"
                        r="25"
                        :border-width="5"
                        background="whitesmoke"
                        :color="color"
                    ></fv-progress-ring>
                    <p class="info-value">{{ usageInfo.cpu_usage.total }}%</p>
                    <p class="info-name">CPU</p>
                </div>
                <div v-if="usageInfo.mem_usage && usageInfo.mem_usage.percent" class="board-unit">
                    <fv-progress-ring
                        :model-value="usageInfo.mem_usage.percent"
                        r="25"
                        :border-width="5"
                        background="whitesmoke"
                        :color="color"
                    ></fv-progress-ring>
                    <p class="info-value">{{ usageInfo.mem_usage.percent }}%</p>
                    <p class="info-name">Memory</p>
                </div>
            </div>
        </div>
    </div>
</template>

<script>
import { mapState } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'
import { useTheme } from '@/stores/theme'
import { useLoopAI } from '@/stores/loopAI'
import timeRounder from '@/components/general/timeRounder.vue'

export default {
    components: {
        timeRounder
    },
    props: {
        modelValue: {
            default: null
        }
    },
    data() {
        return {
            thisValue: this.modelValue,
            inside: false,
            usageInfo: {
                cpu_usage: {},
                gpu_usage: [],
                mem_usage: {}
            },
            timer: {
                state: null
            }
        }
    },
    watch: {
        modelValue() {
            this.thisValue = this.modelValue
        },
        thisValue() {
            this.$emit('update:modelValue', this.thisValue)
        },
        isRunning(val) {
            if (!val) return clearInterval(this.timer.state)
            else this.getStateInit()
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        ...mapState(useTheme, ['theme', 'color']),
        ...mapState(useLoopAI, ['taskStatus']),
        isRunning() {
            return this.taskStatus.running
        }
    },
    mounted() {
        if (this.isRunning) this.getStateInit()
    },
    methods: {
        getStateInit() {
            clearInterval(this.timer.state)
            this.timer.state = setInterval(() => {
                this.$api.starter
                    .getHardwareUsage()
                    .then((res) => {
                        if (res.code === 200) {
                            this.usageInfo = res.data
                        }
                    })
                    .catch((e) => {})
            }, 5000)
        },
        copyText(content) {
            navigator.clipboard.writeText(content).then(() => {
                this.$barWarning(this.local('Copy Success'))
            })
        }
    }
}
</script>

<style lang="scss">
.df-current-task-container {
    @include VcenterC;

    position: absolute;
    left: 15px;
    width: auto;
    height: auto;
    max-width: 120px;
    gap: 5px;
    padding: 15px;
    background: rgba(255, 255, 255, 0.3);
    border: rgba(120, 120, 120, 0.2) solid thin;
    border-radius: 16px;
    flex-direction: column;
    transition:
        background 0.3s ease-out,
        transform 0.3s ease-in-out,
        max-width 0.8s ease-out;
    backdrop-filter: blur(10px);
    box-shadow: 0px 0px 1px rgba(0, 0, 0, 0.1);
    z-index: 30;

    &.dark {
        background: rgba(9, 9, 9, 0.3);
        border: rgba(255, 255, 255, 0.1) solid thin;

        &:hover {
            background: rgba(9, 9, 9, 0.1);
        }
    }

    &.template {
        height: 50px;
    }

    &:hover {
        max-width: 50%;
        background: rgba(250, 250, 250, 0.6);
        transform: scale(1.05);
    }

    &:active {
        transform: scale(0.99);
    }

    .row-item {
        @include nowrap;
        @include Vcenter;

        position: relative;
        width: 100%;
        gap: 15px;
        height: auto;
        flex-shrink: 0;
    }

    .board-item-block {
        @include HbetweenVcenter;

        position: relative;
        width: 100%;
        height: auto;
        padding: 5px;
        background: rgba(255, 255, 255, 0.6);
        border-radius: 8px;
        overflow-x: overlay;

        .board-unit {
            @include HcenterVcenter;

            position: relative;
            width: 50px;
            margin-bottom: 15px;

            .info-value {
                position: absolute;
                font-size: 12px;
                font-weight: bold;
                color: #333;
                transition: all 0.3s;
                user-select: none;
            }

            .info-name {
                position: absolute;
                bottom: -15px;
                font-size: 10px;
                color: rgba(120, 120, 120, 1);
                user-select: none;
            }
        }
    }

    .df-current-task-title {
        @include nowrap;
        @include pink-a;

        font-size: 12px;
        font-weight: bold;
        color: #333;
        transition: all 0.3s;
        user-select: none;
    }

    .df-current-sec-info {
        @include nowrap;

        font-size: 10px;
        color: rgba(120, 120, 120, 1);
        user-select: none;
    }
}

.df-cp-scale-up-to-up-enter-active {
    animation: scaleUp 0.3s ease both;
    animation-delay: 0.3s;
}

.df-cp-scale-up-to-up-leave-active {
    position: absolute;
    width: 100%;
    height: 100%;
    display: flex;
    justify-content: center;
    align-items: center;
    animation: scaleDownUp 0.1s ease both;
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
