<template>
    <div class="looper-command-preview">
        <div class="looper-command-header">
            <div class="looper-command-badge">
                <span class="looper-command-badge-label">{{ local('Operation') }}</span>
                <span class="looper-command-badge-value">{{ displayOp }}</span>
            </div>
        </div>
        <div v-if="hasMessage" class="looper-command-message">
            <md-text-block :model-value="commandMessage"></md-text-block>
        </div>
        <p v-else class="looper-command-empty">
            {{ local('No Message') }}
        </p>
    </div>
</template>

<script>
import { mapState } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'

import mdTextBlock from '@/components/general/mdTextBlock.vue'

export default {
    components: { mdTextBlock },
    props: {
        modelValue: {
            type: [Object, String],
            default: () => ({})
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        parsedValue() {
            if (typeof this.modelValue === 'string') {
                try {
                    return JSON.parse(this.modelValue)
                } catch (error) {
                    return {}
                }
            }
            if (this.modelValue && typeof this.modelValue === 'object') return this.modelValue
            return {}
        },
        commandOp() {
            if (typeof this.parsedValue.op === 'string') return this.parsedValue.op
            return ''
        },
        displayOp() {
            if (!this.commandOp) return this.local('Unknown')
            return this.commandOp
        },
        commandMessage() {
            if (typeof this.parsedValue.message === 'string') return this.parsedValue.message
            return ''
        },
        hasMessage() {
            return !!this.commandMessage
        }
    }
}
</script>

<style lang="scss">
.looper-command-preview {
    width: 100%;
    padding: 10px 12px;
    background: var(--lp-surface);
    border: 1px solid var(--lp-line);
    border-radius: 12px;
    backdrop-filter: blur(8px);
    box-sizing: border-box;

    .looper-command-header {
        width: 100%;
        margin-bottom: 8px;
        display: flex;
        align-items: center;
    }

    .looper-command-badge {
        width: auto;
        max-width: 100%;
        padding: 3px 6px;
        gap: 6px;
        border-radius: 999px;
        display: inline-flex;
        align-items: center;
        font-size: 8px;
        font-weight: bold;
        color: rgba(8, 145, 178, 1);
        background: var(--lp-surface);
    }

    .looper-command-badge-label {
        color: rgba(14, 116, 144, 0.9);
        white-space: nowrap;
        user-select: none;
    }

    .looper-command-badge-value {
        color: var(--lp-text);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    .looper-command-message {
        width: 100%;
        padding: 2px 4px;
        font-size: 8px;
        color: var(--lp-text);
        overflow: hidden;

        * {
            font-size: 8px;
        }
    }

    .looper-command-empty {
        width: 100%;
        padding: 2px 4px;
        font-size: 8px;
        color: var(--lp-text-mute);
        white-space: pre-wrap;
    }
}
</style>
