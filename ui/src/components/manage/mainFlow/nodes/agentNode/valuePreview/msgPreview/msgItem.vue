<template>
    <div class="msg-preview-item" :class="[`role-${normalizedRole}`]">
        <div class="msg-preview-header">
            <div class="msg-preview-role-badge">
                <span class="msg-preview-role-dot"></span>
                <span class="msg-preview-role-text">{{ displayRole }}</span>
            </div>
        </div>
        <div class="msg-preview-content">
            <md-text-block :model-value="contentValue"></md-text-block>
        </div>
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
            type: [String, Object],
            default: ''
        },
        role: {
            type: String,
            default: ''
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        contentValue() {
            if (typeof this.modelValue === 'string') return this.modelValue
            if (this.modelValue && typeof this.modelValue.content === 'string')
                return this.modelValue.content
            if (this.modelValue === null || this.modelValue === undefined) return ''
            try {
                return JSON.stringify(this.modelValue, null, 2)
            } catch (error) {
                return String(this.modelValue)
            }
        },
        normalizedRole() {
            const role = (this.role || 'message').toLowerCase()
            if (['user', 'assistant', 'system', 'tool'].includes(role)) return role
            return 'message'
        },
        displayRole() {
            const role = this.normalizedRole
            if (role === 'assistant') return this.local('Assistant')
            if (role === 'user') return this.local('User')
            if (role === 'system') return this.local('System')
            if (role === 'tool') return this.local('Tool')
            return this.local('Message')
        }
    }
}
</script>

<style lang="scss">
.msg-preview-item {
    width: 100%;
    padding: 10px 12px;
    background: rgba(255, 255, 255, 0.72);
    border: 1px solid rgba(160, 160, 160, 0.18);
    border-radius: 12px;
    backdrop-filter: blur(8px);
    box-sizing: border-box;

    .msg-preview-header {
        width: 100%;
        margin-bottom: 8px;
        display: flex;
        align-items: center;
    }

    .msg-preview-role-badge {
        width: auto;
        max-width: 100%;
        padding: 3px 6px;
        gap: 6px;
        border-radius: 999px;
        display: inline-flex;
        align-items: center;
        font-size: 8px;
        font-weight: bold;
        color: rgba(161, 147, 202, 1);
        background: rgba(203, 190, 255, 0.35);
    }

    .msg-preview-role-dot {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: currentColor;
        flex-shrink: 0;
    }

    .msg-preview-role-text {
        white-space: nowrap;
        user-select: none;
    }

    .msg-preview-content {
        width: 100%;
        padding: 2px 4px;
        font-size: 8px;
        color: rgba(55, 65, 81, 1);
        overflow: hidden;

        * {
            font-size: 8px;
        }
    }

    &.role-assistant {
        .msg-preview-role-badge {
            color: rgba(8, 145, 178, 1);
            background: rgba(207, 250, 254, 0.95);
        }
    }

    &.role-system {
        .msg-preview-role-badge {
            color: rgba(124, 58, 237, 1);
            background: rgba(237, 233, 254, 0.95);
        }
    }

    &.role-tool {
        .msg-preview-role-badge {
            color: rgba(217, 119, 6, 1);
            background: rgba(254, 243, 199, 0.95);
        }
    }
}
</style>
