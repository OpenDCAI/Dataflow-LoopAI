<template>
    <div v-show="visible" class="looper-takeover-warning">
        <fv-button
            theme="dark"
            class="interrupt-button"
            background="linear-gradient(135deg, rgba(255, 204, 77, 1), rgba(242, 166, 34, 1))"
            border-radius="50"
            font-size="12"
            style="width: 30px"
            @click="handleLooperTakeoverInterrupt"
        >
            <i class="ms-Icon ms-Icon--Warning"></i>
        </fv-button>
        <p class="warning-text">
            {{ local('Looper will take over in') }}
            <span>{{ seconds }}s</span>
            {{ local('for the next action.') }}
        </p>
    </div>
</template>

<script>
import { mapActions, mapState } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'
import { useLoopAI } from '@/stores/loopAI'

export default {
    name: 'LooperTakeoverWarning',
    watch: {
        'msgStreamModel.loading'(val, oldVal) {
            if (val) {
                this.clearLooperTakeoverCountdown({
                    keepActive: true
                })
                return
            }
            if (oldVal && !val && this.looperTakeover.active) {
                this.startLooperTakeoverCountdown()
            }
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        ...mapState(useLoopAI, ['currentTask', 'taskStatus', 'msgStreamModel', 'looperTakeover']),
        visible() {
            return !this.msgStreamModel.loading && this.looperTakeover.active && !this.runningMe
        },
        seconds() {
            return this.looperTakeover.seconds
        },
        runningMe() {
            try {
                return this.taskStatus.node_status.some((node) => {
                    return ['looper'].includes(node.node_name) && node.status === 'running'
                })
            } catch (e) {
                return false
            }
        }
    },
    methods: {
        ...mapActions(useLoopAI, [
            'getStatus',
            'clearLooperTakeoverCountdown',
            'setLooperTakeoverCountdown'
        ]),
        startLooperTakeoverCountdown() {
            if (!this.currentTask?.task_id || !this.looperTakeover.active) return
            this.clearLooperTakeoverCountdown({
                resetSeconds: false,
                keepActive: true
            })
            this.setLooperTakeoverCountdown({
                seconds: this.looperTakeover.duration,
                duration: this.looperTakeover.duration,
                active: true
            })
            this.looperTakeover.timer = setInterval(async () => {
                if (this.msgStreamModel.loading || !this.looperTakeover.active) {
                    this.clearLooperTakeoverCountdown({
                        resetSeconds: false,
                        keepActive: true
                    })
                    return
                }
                if (this.looperTakeover.seconds <= 1) {
                    this.clearLooperTakeoverCountdown({
                        keepActive: true
                    })
                    await this.runLooperTakeover()
                    return
                }
                this.setLooperTakeoverCountdown({
                    seconds: this.looperTakeover.seconds - 1,
                    duration: this.looperTakeover.duration,
                    active: true
                })
            }, 1000)
        },
        handleLooperTakeoverInterrupt() {
            this.clearLooperTakeoverCountdown()
            this.$barWarning(this.local('Looper takeover interrupted.'), {
                status: 'warning'
            })
        },
        async runLooperTakeover() {
            let session_id = this.currentTask?.task_id
            if (!session_id) return
            try {
                const res = await this.$api.starter.starterCodexSessionLooper(session_id, {})
                if (res.code === 200) {
                    await this.getStatus(session_id)
                    return
                }
                this.$barWarning(res.message || this.local('Failed to let Looper take over.'), {
                    status: 'warning'
                })
            } catch (error) {
                this.$barWarning(error.message, {
                    status: 'error'
                })
            }
        }
    },
    beforeUnmount() {
        this.clearLooperTakeoverCountdown({
            resetSeconds: false
        })
    }
}
</script>

<style lang="scss">
.looper-takeover-warning {
    flex: 1;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 4px 10px 4px 4px;
    border-radius: 999px;
    background: rgba(255, 245, 204, 0.96);
    border: rgba(242, 166, 34, 0.35) solid thin;
    box-shadow: 0px 4px 16px rgba(242, 166, 34, 0.18);

    .interrupt-button {
        height: 28px;
        color: rgba(92, 54, 0, 1);
        font-weight: 600;
    }

    .warning-text {
        margin: 0;
        font-size: 12px;
        color: rgba(122, 73, 0, 1);
        white-space: nowrap;

        span {
            font-weight: 700;
            color: rgba(166, 92, 0, 1);
        }
    }
}
</style>
