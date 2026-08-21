<template>
    <section class="lp-transcript lp-sheet">
        <header class="lp-sheet__head">
            <span class="lp-label">{{ local('Transcript') }}</span>
            <div class="lp-transcript__spacer"></div>
            <span v-if="currentTask" class="lp-transcript__id">{{ currentTask.task_id }}</span>
        </header>

        <div ref="list" class="lp-sheet__body lp-transcript__body">
            <template v-for="(msg, index) in taskMessages" :key="index">
                <msg-block v-if="showMe(msg)" :model-value="msg" />
            </template>

            <response-block v-show="msgStreamModel.loading"></response-block>

            <p v-if="msgStreamModel.status === 'failed'" class="lp-transcript__failed">
                <span class="lp-dot is-failed"></span>
                {{ local('Run failed. Send again to retry.') }}
            </p>

            <p v-if="!taskMessages.length && !msgStreamModel.loading" class="lp-empty">
                {{ local('Nothing yet. Whatever you send below starts the loop.') }}
            </p>
        </div>
    </section>
</template>

<script>
import { useAppConfig } from '@/stores/appConfig'
import { useLoopAI } from '@/stores/loopAI'

import msgBlock from './msgBlock.vue'
import responseBlock from './responseBlock/index.vue'
import { mapState } from 'pinia'

export default {
    name: 'MsgList',
    components: {
        msgBlock,
        responseBlock
    },
    watch: {
        'msgStreamModel.loading'() {
            this.scrollToEnd()
        },
        'taskMessages.length'() {
            this.scrollToEnd()
        },
        currentMsg() {
            this.scrollToEnd()
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        ...mapState(useLoopAI, ['taskMessages', 'msgStreamModel', 'currentMsg', 'currentTask'])
    },
    methods: {
        scrollToEnd() {
            this.$nextTick(() => {
                const list = this.$refs.list
                if (list) list.scrollTop = list.scrollHeight
            })
        },
        showMe(msg) {
            return (
                !msg.data.tool_calls ||
                msg.data.tool_calls.length === 0 ||
                (msg.data.tool_calls.length > 0 && msg.data.content)
            )
        }
    }
}
</script>

<style lang="scss">
.lp-transcript {
    width: 404px;
    flex-shrink: 0;

    .lp-transcript__spacer {
        flex: 1;
    }

    .lp-transcript__id {
        font-family: var(--lp-mono);
        font-size: var(--lp-t-xs);
        color: var(--lp-text-faint);
    }

    .lp-transcript__body {
        gap: 18px;
        display: flex;
        flex-direction: column;
        overscroll-behavior: contain;
    }

    .lp-transcript__failed {
        gap: 8px;
        font-family: var(--lp-mono);
        font-size: var(--lp-t-sm);
        color: var(--lp-err);
        display: flex;
        align-items: center;
    }

    &.is-wide {
        width: 100%;
        border-left: none;
    }
}
</style>
