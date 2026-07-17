<template>
    <div class="msg-preview-list">
        <div class="msg-preview-toggle-row">
            <fv-button
                :theme="!expanded ? 'dark' : 'light'"
                :border-radius="999"
                class="msg-preview-toggle"
                :title="toggleTitle"
                :font-size="10"
                @click="expanded = !expanded"
            >
                <span class="msg-preview-toggle-text">{{ toggleText }}</span>
                <i
                    class="ms-Icon"
                    :class="[`ms-Icon--${expanded ? 'ChevronUpMed' : 'ChevronDownMed'}`]"
                ></i>
            </fv-button>
        </div>
        <msg-item
            v-for="(item, index) in visibleMessages"
            :key="`${item.role || 'message'}-${index}`"
            :model-value="item.content"
            :role="item.role"
        ></msg-item>
    </div>
</template>

<script>
import { mapState } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'

import msgItem from './msgItem.vue'

export default {
    components: { msgItem },
    props: {
        modelValue: {
            type: [Array, String],
            default: () => []
        }
    },
    data() {
        return {
            expanded: false
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        parsedMessages() {
            let raw = this.modelValue
            if (typeof raw === 'string') {
                try {
                    raw = JSON.parse(raw)
                } catch (error) {
                    raw = []
                }
            }
            if (!Array.isArray(raw)) return []
            return raw.map((item) => ({
                role: typeof item?.role === 'string' ? item.role : 'message',
                content: typeof item?.content === 'string' ? item.content : ''
            }))
        },
        showExpandButton() {
            return this.parsedMessages.length > 2
        },
        messageCount() {
            return this.parsedMessages.length
        },
        toggleTitle() {
            if (this.expanded) return this.local('Collapse')
            return `${this.local('Expand')} (${this.messageCount})`
        },
        toggleText() {
            if (this.messageCount === 0) return this.local('No Messages')
            if (this.expanded) return this.local('Collapse')
            return `${this.local('Show Messages')} (${this.messageCount})`
        },
        visibleMessages() {
            if (!this.expanded) return []
            return this.parsedMessages
        }
    }
}
</script>

<style lang="scss">
.msg-preview-list {
    width: 100%;
    gap: 8px;
    display: flex;
    flex-direction: column;

    .msg-preview-toggle-row {
        width: 100%;
        border-radius: 8px;
        display: flex;
        justify-content: center;
    }

    .msg-preview-toggle {
        width: 100%;
        height: 20px;
        gap: 6px;
    }

    .msg-preview-toggle-text {
        font-size: 10px;
        white-space: nowrap;
    }
}
</style>
