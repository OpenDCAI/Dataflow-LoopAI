<template>
    <div class="collaspe-block" @click="isCollapsed = !isCollapsed">
        <i
            class="ms-Icon"
            :class="[`ms-Icon--${isCollapsed ? 'ChevronLeft' : 'ChevronRight'}`]"
        ></i>
    </div>
    <div class="loopai-msg-list-container" ref="list" :class="{ collapsed: isCollapsed }">
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
    data() {
        return {
            isCollapsed: false
        }
    },
    watch: {
        'msgStreamModel.msg'() {
            this.$nextTick(() => {
                this.$refs.list.scrollTop = this.$refs.list.scrollHeight
            })
        },
        'msgStreamModel.loading'() {
            this.$.list.$nextTick(() => {
                this.$refs.list.scrollTop = this.$refs.list.scrollHeight
            })
        },
        'taskMessages.length'() {
            this.$nextTick(() => {
                this.$.list.scrollTop = this.$.list.scrollHeight
            })
        }
    },
    computed: {
        ...mapState(useLoopAI, ['taskMessages', 'msgStreamModel'])
    },
    methods: {
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
.collaspe-block {
    @include HcenterVcenterC;

    position: fixed;
    top: 35px;
    right: 35px;
    width: 30px;
    height: 30px;
    background: rgba(245, 245, 245, 0.8);
    border: rgba(120, 120, 120, 0.1) solid thin;
    border-radius: 8px;
    box-shadow: 0px 1px 3px rgba(0, 0, 0, 0.1);
    transition: all 0.3s;
    z-index: 9;

    &:hover {
        background: rgba(255, 255, 255, 0.8);
    }

    &:active {
        background: rgba(250, 250, 250, 0.8);
    }
}
.loopai-msg-list-container {
    @include HcenterC;

    position: absolute;
    top: 0px;
    right: 0px;
    width: min(450px, 90%);
    height: 100%;
    padding: 95px 15px;
    overflow: auto;
    transition: width 0.3s;
    z-index: 1;

    &.collapsed {
        width: 0px;
    }
}
</style>
