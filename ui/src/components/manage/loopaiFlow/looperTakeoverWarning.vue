<template>
    <div v-show="visible" class="lp-takeover">
        <span class="lp-dot is-running"></span>
        <p v-show="!runningMe && !waiting" class="lp-takeover__text">
            {{ local('Looper takes over in') }}
            <span>{{ seconds }}s</span>
            {{ local('unless you type.') }}
        </p>
        <p v-show="!runningMe && waiting" class="lp-takeover__text">
            {{ local('Waiting for response...') }}
        </p>
        <p v-show="runningMe" class="lp-takeover__text">
            {{ local('Looper executing...') }}
        </p>
        <button
            type="button"
            class="lp-btn lp-btn--ghost lp-btn--sm lp-takeover__action"
            @click="handleLooperTakeoverInterrupt"
        >
            {{ runningMe ? local('Cancel') : local('Hold') }}
        </button>
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
            return !this.msgStreamModel.loading && this.looperTakeover.active
        },
        seconds() {
            return this.looperTakeover.seconds
        },
        waiting() {
            return this.looperTakeover.duration === this.looperTakeover.seconds
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
            if (this.runningMe) {
                this.looperCancel()
                return
            }
            this.looperInterrupt()
        },
        looperInterrupt() {
            this.clearLooperTakeoverCountdown()
            this.$barWarning(this.local('Looper takeover interrupted.'), {
                status: 'default'
            })
        },
        async looperCancel() {
            let session_id = this.currentTask?.task_id
            if (!session_id) return
            try {
                const res = await this.$api.starter.starterCodexSessionLooperTerminate(session_id)
                if (res.code === 200) {
                    this.clearLooperTakeoverCountdown()
                    this.$barWarning(this.local('Looper takeover canceled.'), {
                        status: 'default'
                    })
                    return
                }
                this.$barWarning(res.message || this.local('Failed to cancel Looper takeover.'), {
                    status: 'warning'
                })
            } catch (error) {
                this.$barWarning(error.message, {
                    status: 'error'
                })
            }
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
.lp-takeover {
    gap: 8px;
    display: flex;
    align-items: center;
    min-width: 0;

    .lp-takeover__text {
        font-family: var(--lp-mono);
        font-size: var(--lp-t-sm);
        color: var(--lp-run);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;

        span {
            font-weight: 500;
        }
    }

    .lp-takeover__action {
        flex-shrink: 0;
    }
}
</style>
