<template>
    <div class="loopai-msg-list-container">
        <msg-block
            v-show="showMe(msg)"
            v-for="(msg, index) in taskMessages"
            :key="index"
            :model-value="msg"
        />
        <msg-block
            v-if="msgStreamModel.msg"
            :model-value="msgStreamModel.msg[0]"
            :loadingMsg="true"
        />
    </div>
</template>

<script>
import { useLoopAI } from '@/stores/loopAI'

import msgBlock from './msgBlock.vue'
import { mapState } from 'pinia'

export default {
    components: {
        msgBlock
    },
    props: {},
    watch: {
        'msgStreamModel.msg'() {
            this.$el.scrollTop = this.$el.scrollHeight
        },
        'msgStreamModel.loading'() {
            this.$el.scrollTop = this.$el.scrollHeight
        },
        'taskMessages.length'() {
            this.$nextTick(() => {
                this.$el.scrollTop = this.$el.scrollHeight
            })
        }
    },
    computed: {
        ...mapState(useLoopAI, ['taskMessages', 'msgStreamModel'])
    },
    methods: {
        showMe(msg) {
            return !msg.data.tool_calls || msg.data.tool_calls.length === 0
        }
    }
}
</script>

<style lang="scss">
.loopai-msg-list-container {
    @include HcenterC;

    position: absolute;
    top: 0px;
    right: 0px;
    width: min(450px, 90%);
    height: 100%;
    padding: 135px 15px;
    overflow: auto;
    z-index: 1;
}
</style>
