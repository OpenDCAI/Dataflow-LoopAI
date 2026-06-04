<template>
    <transition name="response-block-expand">
        <div v-if="currentMsg.length > 0" class="response-block">
            <p class="elapsed-time-block">
                {{ local('Processed') }}: {{ computedElapsedTimeSecond.toFixed(0) }}s
            </p>
            <div v-for="(msg, index) in currentMsg" :key="index">
                <component
                    v-if="!msgMapper(msg).default"
                    :is="getComponent(msg)"
                    :model-value="msgMapper(msg)"
                    :loading="index >= currentMsg.length - 1"
                />
                <p v-if="false">{{ msg }}</p>
            </div>
        </div>
    </transition>
</template>

<script>
import { useAppConfig } from '@/stores/appConfig'
import { useLoopAI } from '@/stores/loopAI'

import { mapState } from 'pinia'

import msgBlock from '@/components/manage/chat/msgBlock.vue'
import simpleText from '@/components/manage/chat/responseBlock/simpleText.vue'
import statusBlock from '@/components/manage/chat/responseBlock/statusBlock.vue'

export default {
    components: {
        msgBlock,
        simpleText,
        statusBlock
    },
    props: {},
    data() {
        return {
            startTime: new Date(),
            computedElapsedTimeSecond: 0,
            timer: {
                elapsedSecond: null
            }
        }
    },
    watch: {
        'msgStreamModel.loading'(val) {
            if (val) {
                this.startTime = new Date()
                this.timerInit()
            } else {
                clearInterval(this.timer.elapsedSecond)
            }
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        ...mapState(useLoopAI, ['msgStreamModel', 'currentMsg'])
    },
    methods: {
        timerInit() {
            clearInterval(this.timer.elapsedSecond)
            this.timer.elapsedSecond = setInterval(() => {
                this.computedElapsedTimeSecond = this.getElapsedTimeSecond()
            }, 1000)
        },
        getElapsedTimeSecond() {
            return (new Date() - this.startTime) / 1000
        },
        getComponent(msg) {
            if (['starter.codex.init'].includes(msg.type)) return statusBlock
            if (['runner.started', 'completed'].includes(msg.type)) return msgBlock
            if (msg.type === 'event') {
                const event = msg?.event || {}
                if (['item.completed', 'item.started'].includes(msg?.event?.type)) {
                    if (event.item?.type === 'agent_message') return msgBlock
                    if (['command_execution', 'file_change'].includes(event.item?.type))
                        return simpleText
                }
            }
            return simpleText
        },
        msgMapper(msg) {
            if (msg.type === 'submitted')
                return {
                    title: this.local('Status'),
                    content: msg.message
                }

            if (['starter.codex.init'].includes(msg.type)) {
                return {
                    content: [
                        {
                            key: 'model',
                            value: msg.model
                        },
                        {
                            key: 'base_url',
                            value: msg.base_url
                        }
                    ]
                }
            }
            if (['runner.started'].includes(msg.type)) {
                return {
                    type: 'user',
                    data: {
                        content: msg.prompt
                    }
                }
            }
            if (msg.type === 'event') {
                const event = msg?.event || {}
                if (['item.completed', 'item.started'].includes(msg?.event?.type)) {
                    if (event.item?.type === 'agent_message')
                        return {
                            type: 'assistant',
                            data: {
                                content: event.item?.text
                            }
                        }
                    if (event.item?.type === 'command_execution')
                        return {
                            title: this.local('Command Execution'),
                            content: event.item?.command
                        }
                    if (event.item?.type === 'file_change')
                        return {
                            title: this.local('File Change'),
                            content: event.item?.changes?.[0]?.path
                        }
                }
                if (['error'].includes(event.type))
                    return {
                        title: '',
                        content: event?.message
                    }
            }
            if (['completed'].includes(msg.type)) {
                return {
                    type: 'assistant',
                    data: {
                        content: msg.result?.finalResponse || ''
                    }
                }
            }
            return {
                title: msg.type,
                content: '',
                default: true
            }
        }
    },
    beforeUnmount() {
        clearInterval(this.timer.elapsedSecond)
    }
}
</script>

<style lang="scss">
.response-block {
    position: relative;
    width: 100%;
    height: auto;
    padding: 15px;
    flex-shrink: 0;
    background: rgba(245, 236, 243, 0.65);
    border-radius: 8px;
    gap: 10px;
    display: flex;
    flex-direction: column;
    backdrop-filter: blur(3px);
    -webkit-backdrop-filter: blur(3px);
    overflow: hidden;

    .elapsed-time-block {
        font-size: 12px;
        font-weight: bold;
        color: rgba(39, 38, 37, 1);
        user-select: none;
    }

    .msg-block:last-child {
        margin-bottom: 0px;
    }
}

.response-block-expand-enter-active,
.response-block-expand-leave-active {
    overflow: hidden;
    transition:
        max-height 0.28s ease,
        opacity 0.2s ease,
        transform 0.28s ease;
}

.response-block-expand-enter-from,
.response-block-expand-leave-to {
    max-height: 0;
    opacity: 0;
    transform: translateY(-6px);
}

.response-block-expand-enter-to,
.response-block-expand-leave-from {
    max-height: 1200px;
    opacity: 1;
    transform: translateY(0);
}
</style>
