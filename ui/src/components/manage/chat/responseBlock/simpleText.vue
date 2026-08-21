<template>
    <div
        class="lp-trace"
        :class="[{ 'is-running': loading }]"
        :title="`${thisValue.title || ''} ${thisValue.content || ''}`.trim()"
    >
        <span class="lp-trace__marker"></span>
        <span v-if="thisValue.title" class="lp-trace__title">{{ thisValue.title }}</span>
        <span class="lp-trace__content" @click="copyTextContent(thisValue.content)">
            {{ thisValue.content }}
        </span>
    </div>
</template>

<script>
export default {
    name: 'TraceLine',
    props: {
        modelValue: {
            type: Object,
            default: () => ({})
        },
        loading: {
            type: Boolean,
            default: false
        }
    },
    data() {
        return {
            thisValue: this.modelValue
        }
    },
    watch: {
        modelValue() {
            this.thisValue = this.modelValue
        }
    },
    methods: {
        copyTextContent(text) {
            if (typeof text === 'object') text = JSON.stringify(text)
            if (!text) return
            navigator.clipboard.writeText(text)
        }
    }
}
</script>

<style lang="scss">
/* One line per step the agent took: a marker, what it was, what it touched. */
.lp-trace {
    width: 100%;
    gap: 8px;
    font-family: var(--lp-mono);
    font-size: var(--lp-t-sm);
    line-height: 1.8;
    color: var(--lp-text-mute);
    display: flex;
    align-items: baseline;
    overflow: hidden;

    .lp-trace__marker {
        width: 4px;
        height: 4px;
        background: var(--lp-line-hi);
        border-radius: var(--lp-r-full);
        flex-shrink: 0;
        transform: translateY(-2px);
    }

    .lp-trace__title {
        flex-shrink: 0;
        color: var(--lp-text-dim);
    }

    .lp-trace__content {
        flex: 1;
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        cursor: copy;

        &:hover {
            color: var(--lp-text-dim);
        }
    }

    &.is-running {
        color: var(--lp-text-dim);

        .lp-trace__marker {
            background: var(--lp-run);
            animation: lp-pulse 1.6s var(--lp-ease) infinite;
        }
    }
}
</style>
