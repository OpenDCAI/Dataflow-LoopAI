<template>
    <div class="lp-status-block">
        <span class="lp-label">{{ local('Info') }}</span>
        <div class="lp-status-block__rows">
            <div
                v-for="(item, index) in modelValue.content"
                :key="index"
                class="lp-status-block__row"
                :title="local('Copy')"
                @click="copyTextContent(item.value)"
            >
                <span class="lp-status-block__key">{{ item.key }}</span>
                <span class="lp-status-block__value">{{ item.value }}</span>
            </div>
        </div>
    </div>
</template>

<script>
import { useAppConfig } from '@/stores/appConfig.js'
import { mapState } from 'pinia'

export default {
    name: 'StatusBlock',
    props: {
        modelValue: {
            type: Object,
            default: () => ({})
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local'])
    },
    methods: {
        copyTextContent(text) {
            if (typeof text === 'object') text = JSON.stringify(text)
            navigator.clipboard.writeText(text)
        }
    }
}
</script>

<style lang="scss">
.lp-status-block {
    position: relative;
    width: 100%;
    gap: 8px;
    flex-shrink: 0;
    display: flex;
    flex-direction: column;

    .lp-status-block__rows {
        padding: 8px 10px;
        gap: 4px;
        background: var(--lp-chrome);
        border: 1px solid var(--lp-line);
        border-radius: var(--lp-r-2);
        display: flex;
        flex-direction: column;
    }

    .lp-status-block__row {
        gap: 10px;
        font-family: var(--lp-mono);
        font-size: var(--lp-t-sm);
        line-height: 1.7;
        display: flex;
        cursor: copy;

        &:hover .lp-status-block__value {
            color: var(--lp-text);
        }
    }

    .lp-status-block__key {
        width: 84px;
        flex-shrink: 0;
        color: var(--lp-text-mute);
    }

    .lp-status-block__value {
        flex: 1;
        min-width: 0;
        color: var(--lp-text-dim);
        overflow-wrap: anywhere;
    }
}
</style>
