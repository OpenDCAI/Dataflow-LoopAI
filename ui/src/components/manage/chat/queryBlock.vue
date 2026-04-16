<template>
    <div class="editor-margin-wrapper" :class="[{ 'running-shadow': runningLLM }]">
        <div class="bg-wrapper">
            <p class="top-title">{{ local('Chat with LoopAI') }}</p>
            <power-editor
                :placeholder="placeholder"
                :language="language == 'zh' ? 'cn' : 'en'"
                :theme="theme"
                class="power-editor-block"
                ref="editor"
                :showToolBar="thisFullScreenEditor"
                :editorBackground="theme === 'dark' ? 'rgba(52, 64, 84, 1)' : 'white'"
                :editorOutSideBackground="theme === 'dark' ? 'rgba(52, 64, 84, 1)' : 'white'"
                :editablePaddingBottom="thisFullScreenEditor ? 315 : 0"
                :imgInterceptor="imgInterceptor"
                style="height: 100%; flex: 1"
            ></power-editor>
        </div>

        <fv-button
            :border-radius="50"
            :is-box-shadow="true"
            style="position: absolute; width: 30px; height: 30px; bottom: 12px; right: 100px"
            @click="thisFullScreenEditor = !thisFullScreenEditor"
        >
            <i
                class="ms-Icon"
                :class="[`ms-Icon--${thisFullScreenEditor ? 'BackToWindow' : 'FullScreen'}`]"
            ></i>
        </fv-button>
        <fv-button
            :theme="theme"
            class="editor-submit-button"
            border-radius="8"
            :reveal-border-gradient-list="[
                'rgba(129, 208, 246, 1)',
                'rgba(146, 156, 218, 1)',
                'rgba(226, 121, 162, 0.8)',
                'rgba(255, 255, 255, 0.1)'
            ]"
            :disabled="holdon"
            style="margin-left: 5px"
            @click="submitQuery"
            >{{ local('Submit') }}</fv-button
        >
    </div>
</template>

<script>
import { mapActions, mapState } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'
import { useTheme } from '@/stores/theme'
import { useLoopAI } from '@/stores/loopAI'

export default {
    props: {
        fullScreenEditor: {
            type: Boolean,
            default: false
        },
        theme: {
            type: String,
            default: 'light'
        }
    },
    data() {
        return {
            thisFullScreenEditor: this.fullScreenEditor,
            lock: {
                submit: true
            }
        }
    },
    watch: {
        fullScreenEditor(newVal) {
            this.thisFullScreenEditor = newVal
        },
        thisFullScreenEditor(newVal) {
            this.$emit('update:fullScreenEditor', newVal)
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local', 'language']),
        ...mapState(useTheme, ['color']),
        ...mapState(useLoopAI, ['taskStatus', 'msgStreamModel']),
        placeholder() {
            if (this.taskStatus.interrupt_value) return this.taskStatus.interrupt_value
            return this.local(`Ask me anything (Press Ctrl + Enter)`)
        },
        holdon() {
            return !this.taskStatus.running || this.msgStreamModel.loading || !this.lock.submit
        },
        runningLLM() {
            try {
                return this.taskStatus.running_tasks.includes('llm_node')
            } catch (e) {
                return false
            }
        }
    },
    mounted() {
        this.eventInit()
    },
    methods: {
        ...mapActions(useLoopAI, ['getStatus', 'getMsgStream']),
        imgInterceptor({ deleteNode }) {
            this.$nextTick(() => {
                deleteNode()
            })
            this.$barWarning(this.local('Sorry, image is not supported in this chat.'), {
                status: 'warning'
            })
        },
        eventInit() {
            this.$refs.editor.$el.removeEventListener('keydown', this.handleSubmitEnterEvent)
            this.$refs.editor.$el.addEventListener('keydown', this.handleSubmitEnterEvent)
        },
        handleSubmitEnterEvent(event) {
            if (event.ctrlKey && event.key === 'Enter') {
                this.submitQuery()
            }
        },
        submitQuery() {
            if (this.msgStreamModel.loading) return
            if (!this.taskStatus.running) return
            if (!this.lock.submit) return
            let msg = this.$refs.editor.saveMarkdown()
            msg = msg.trim()
            if (msg === '') return
            this.lock.submit = false
            this.$api.starter.agentInput(msg).then(async (res) => {
                if (res.code === 200) {
                    this.$refs.editor.editor().commands.setContent('')
                    this.getStatus()
                    this.getMsgStream()
                    this.lock.submit = true
                } else {
                    this.lock.submit = true
                    this.$barWarning(res.message, {
                        status: 'warning'
                    })
                }
            })
        }
    }
}
</script>

<style lang="scss">
.editor-margin-wrapper {
    position: relative;
    width: min(900px, 90%);
    max-width: 900px;
    height: 30px;
    flex: 1;
    margin-top: 15px;
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    box-sizing: border-box;

    &.running-shadow {
        &:before,
        &:after {
            content: '';
            position: absolute;
            top: 0px;
            left: 0px;
            width: calc(100%);
            height: calc(100%);
            background: linear-gradient(
                45deg,
                rgba(226, 121, 162, 1),
                rgba(146, 156, 218, 1),
                rgba(129, 208, 246, 1),
                rgba(239, 192, 48, 1),
                rgba(246, 100, 100, 1),
                rgba(226, 121, 162, 1),
                rgba(146, 156, 218, 1),
                rgba(129, 208, 246, 1),
                rgba(239, 192, 48, 1),
                rgba(246, 100, 100, 1)
            );
            background-size: 400%;
            border-radius: 20px;
            z-index: -1;
            animation: shadow 20s linear infinite;
        }

        &:after {
            top: -8px;
            left: -8px;
            width: calc(100% + 16px);
            height: calc(100% + 16px);
            filter: blur(24px);
            opacity: 0.9;
        }
    }

    @keyframes shadow {
        0% {
            background-position: 0 0;
        }
        50.01% {
            background-position: 200% 0;
        }
        100% {
            background-position: 0 0;
        }
    }

    .power-editor-block {
        width: 100%;
        border: rgba(200, 200, 200, 0.1) solid thin;
        border-radius: 20px;
        overflow: hidden;
    }

    .editor-submit-button {
        position: absolute;
        right: 12px;
        bottom: 12px;
    }

    .bg-wrapper {
        position: relative;
        left: 0px;
        top: 0px;
        width: 100%;
        height: 100%;
        padding: 5px;
        gap: 5px;
        background: linear-gradient(
            90deg,
            rgba(129, 208, 246, 1),
            rgba(146, 156, 218, 1),
            rgba(226, 121, 162, 1)
        );
        border-radius: 20px;
        display: flex;
        flex-direction: column;
        box-shadow:
            0px -13px 20px rgba(129, 208, 246, 0.1),
            16px 0px 20px rgba(129, 208, 246, 0.1),
            0px 13px 20px rgba(146, 156, 218, 0.1),
            -16px 0px 20px rgba(226, 121, 162, 0.1);

        .top-title {
            @include Vcenter;

            position: relative;
            width: 100%;
            height: 35px;
            padding: 0px 10px;
            font-size: 15px;
            font-weight: lighter;
            color: rgba(255, 255, 255, 0.9);
            line-height: 50px;
            user-select: none;
        }
    }
}
</style>
