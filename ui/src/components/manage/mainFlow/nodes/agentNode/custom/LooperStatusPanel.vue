<template>
    <div class="looper-status-panel">
        <div class="hero-card">
            <div class="hero-top">
                <div class="hero-ring">
                    <fv-progress-ring
                        :model-value="progressPercent"
                        :loading="statusLevel === 'running'"
                        :r="22"
                        :border-width="4"
                        background="rgba(140, 140, 140, 0.14)"
                        :color="foreground"
                    ></fv-progress-ring>
                    <div class="ring-center">
                        <span class="ring-percent">{{ progressPercentText }}</span>
                    </div>
                </div>
                <div class="hero-copy">
                    <div class="status-row">
                        <span class="status-chip" :class="statusLevel">{{ displayStatus }}</span>
                        <span class="current-text" :title="currentText">{{ currentText }}</span>
                    </div>
                    <p class="hero-message">
                        {{ statusMessage }}
                    </p>
                </div>
            </div>
            <div class="hero-meta">
                <div class="hero-stats">
                    <span class="hero-stat">{{ displayStatus }}</span>
                    <span v-if="hasProgressCount" class="hero-stat">{{ progressCountText }}</span>
                </div>
            </div>
        </div>

        <div class="section">
            <div class="section-title">
                <span>{{ local('Command') }}</span>
            </div>
            <looper-command-preview :model-value="commandValue"></looper-command-preview>
        </div>

        <div class="section">
            <div class="section-title">
                <span>{{ local('History Summary') }}</span>
            </div>
            <div class="summary-card">
                <md-text-block :model-value="historySummary"></md-text-block>
            </div>
        </div>
    </div>
</template>

<script>
import { mapState } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'
import { useLoopAI } from '@/stores/loopAI'

import mdTextBlock from '@/components/general/mdTextBlock.vue'
import looperCommandPreview from '../valuePreview/looperCommandPreview.vue'

export default {
    components: { mdTextBlock, looperCommandPreview },
    props: {
        foreground: {
            type: String,
            default: 'rgba(90, 45, 133, 1)'
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        ...mapState(useLoopAI, ['taskStatus']),
        looperStatus() {
            return this.taskStatus?.custom_info?.['looper.looper.status'] || {}
        },
        progressValue() {
            const value = Number(this.looperStatus.progress)
            if (!Number.isFinite(value)) return 0
            return Math.max(0, Math.min(1, value))
        },
        progressPercent() {
            return this.progressValue * 100
        },
        progressPercentText() {
            return `${Math.round(this.progressValue * 100)}%`
        },
        statusMessage() {
            return this.looperStatus.message || this.local('No Message')
        },
        statusValue() {
            return String(this.looperStatus.status || '').toLowerCase()
        },
        statusLevel() {
            if (this.statusValue === 'completed') return 'completed'
            if (this.statusValue === 'failed') return 'failed'
            if (this.statusValue === 'running') return 'running'
            return 'pending'
        },
        displayStatus() {
            if (this.statusValue === 'completed') return this.local('Completed')
            if (this.statusValue === 'failed') return this.local('Failed')
            if (this.statusValue === 'running') return this.local('Running')
            return this.local('Pending')
        },
        currentText() {
            return this.looperStatus.current || 'looper.status'
        },
        commandValue() {
            return this.looperStatus?.data?.command || {}
        },
        historySummary() {
            return this.looperStatus?.data?.historySummary || this.local('No Message')
        },
        progressNum() {
            const value = Number(this.looperStatus.progress_num)
            return Number.isFinite(value) ? value : null
        },
        totalNum() {
            const value = Number(this.looperStatus.total)
            return Number.isFinite(value) ? value : null
        },
        hasProgressCount() {
            return this.progressNum !== null || this.totalNum !== null
        },
        progressCountText() {
            const current = this.progressNum !== null ? this.progressNum : '-'
            const total = this.totalNum !== null ? this.totalNum : '-'
            return `${this.local('Progress')}: ${current} / ${total}`
        }
    }
}
</script>

<style lang="scss" scoped>
.looper-status-panel {
    width: 100%;
    padding: 8px 6px 10px;
    box-sizing: border-box;
}

.hero-card {
    padding: 12px;
    border-radius: 14px;
    background:
        radial-gradient(circle at top left, var(--lp-surface), var(--lp-surface)),
        linear-gradient(145deg, rgba(161, 145, 206, 0.12), rgba(88, 196, 255, 0.08));
    border: 1px solid rgba(90, 45, 133, 0.1);
    box-shadow: 0 10px 24px rgba(42, 51, 70, 0.08);
    backdrop-filter: blur(10px);
}

.hero-top {
    display: flex;
    align-items: flex-start;
    gap: 12px;
}

.hero-ring {
    position: relative;
    width: 52px;
    height: 52px;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
}

.ring-center {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
}

.ring-percent {
    font-size: 10px;
    font-weight: 700;
    color: var(--lp-text);
}

.hero-copy {
    min-width: 0;
    flex: 1;
}

.status-row {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 6px;
}

.status-chip {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    height: 18px;
    padding: 0 6px;
    border-radius: 999px;
    font-size: 10px;
    font-weight: 700;
    color: var(--lp-text);
    background: var(--lp-raised);

    &.running {
        background: rgba(217, 119, 6, 0.95);
    }

    &.completed {
        background: rgba(34, 163, 92, 0.95);
    }

    &.failed {
        background: rgba(209, 52, 56, 0.95);
    }
}

.current-text {
    min-width: 0;
    font-size: 10px;
    color: var(--lp-text);
    line-height: 1.35;
    overflow-wrap: anywhere;
}

.hero-message {
    margin: 0;
    font-size: 11px;
    line-height: 1.45;
    color: var(--lp-text);
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
}

.hero-meta {
    margin-top: 10px;
}

.hero-stats {
    margin-top: 8px;
    gap: 6px;
    display: flex;
    flex-wrap: wrap;
}

.hero-stat {
    display: inline-flex;
    align-items: center;
    min-height: 18px;
    padding: 0 7px;
    border-radius: 999px;
    font-size: 10px;
    color: var(--lp-text);
    background: var(--lp-surface);
    border: 1px solid rgba(90, 45, 133, 0.08);
}

.section {
    margin-top: 10px;
}

.section-title {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 6px;
    font-size: 12px;
    font-weight: 700;
    color: var(--lp-text);
}

.summary-card {
    padding: 10px;
    border-radius: 10px;
    background: var(--lp-surface);
    border: 1px solid rgba(90, 45, 133, 0.12);
    box-sizing: border-box;

    :deep(*) {
        font-size: 10px;
    }
}
</style>
