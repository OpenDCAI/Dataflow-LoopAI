<template>
    <div class="lp-composer" :class="{ 'is-focused': focused }">
        <textarea
            ref="input"
            v-model="draft"
            class="lp-composer__input"
            rows="1"
            spellcheck="false"
            :placeholder="placeholder"
            :disabled="looperActive"
            @focus="focused = true"
            @blur="focused = false"
            @input="autoGrow"
            @keydown="handleKeydown"
        ></textarea>

        <div class="lp-composer__foot">
            <looper-takeover-warning></looper-takeover-warning>
            <p v-if="!looperActive && !currentTask" class="lp-composer__hint">
                {{ local('Creates a task on send') }}
            </p>
            <div class="lp-composer__spacer"></div>
            <div class="lp-composer__keys">
                <span class="lp-kbd">{{ modKeyLabel }}</span>
                <span class="lp-kbd">&#8629;</span>
            </div>
            <button
                type="button"
                class="lp-btn lp-btn--primary"
                :disabled="holdon || !draft.trim()"
                @click="submitQuery"
            >
                {{ local('Send') }}
            </button>
        </div>
    </div>
</template>

<script>
import { mapActions, mapState } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'
import { useLoopAI } from '@/stores/loopAI'

import looperTakeoverWarning from '@/components/manage/loopaiFlow/looperTakeoverWarning.vue'

const MAX_ROWS_HEIGHT = 220

export default {
    name: 'QueryBlock',
    components: {
        looperTakeoverWarning
    },
    data() {
        return {
            draft: '',
            focused: false,
            lock: {
                submit: true
            }
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local', 'language']),
        ...mapState(useLoopAI, ['msgStreamModel', 'currentTask', 'looperTakeover']),
        placeholder() {
            return this.currentTask
                ? this.local('Tell the loop what to do next...')
                : this.local('Describe the model you want. Sending starts the first task.')
        },
        holdon() {
            return !this.lock.submit || this.looperActive
        },
        looperActive() {
            return this.looperTakeover.active
        },
        modKeyLabel() {
            const isApple =
                typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || '')
            return isApple ? '⌘' : 'Ctrl'
        }
    },
    mounted() {
        this.$refs.input?.focus()
    },
    methods: {
        ...mapActions(useLoopAI, [
            'getStatus',
            'createTask',
            'clearLooperTakeoverCountdown',
            'setLooperTakeoverCountdown'
        ]),
        autoGrow() {
            const el = this.$refs.input
            if (!el) return
            el.style.height = 'auto'
            el.style.height = `${Math.min(el.scrollHeight, MAX_ROWS_HEIGHT)}px`
        },
        handleKeydown(event) {
            if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
                event.preventDefault()
                this.submitQuery()
            }
        },
        async submitQuery() {
            if (this.holdon) return
            const msg = this.draft.trim()
            if (!msg) return

            this.clearLooperTakeoverCountdown()
            this.lock.submit = false

            // No task yet? The first thing you send is what creates one.
            let task = this.currentTask
            if (!task?.task_id) {
                task = await this.createTask(msg.slice(0, 48))
                if (!task?.task_id) {
                    this.lock.submit = true
                    return
                }
            }

            const session_id = task.task_id
            this.setLooperTakeoverCountdown({
                seconds: 10,
                duration: 10,
                active: true
            })

            try {
                const res = await this.$api.starter.starterCodexStream({ prompt: msg, session_id })
                if (res.code === 200) {
                    this.draft = ''
                    this.$nextTick(this.autoGrow)
                    await this.getStatus(session_id)
                } else {
                    this.clearLooperTakeoverCountdown()
                    this.$barWarning(res.message, { status: 'warning' })
                }
            } catch (error) {
                this.clearLooperTakeoverCountdown()
                this.$barWarning(error.message, { status: 'error' })
            } finally {
                this.lock.submit = true
            }
        }
    }
}
</script>

<style lang="scss">
.lp-composer {
    position: relative;
    width: 100%;
    padding: 10px 12px;
    gap: 10px;
    background: var(--lp-surface);
    border: 1px solid var(--lp-line-hi);
    border-radius: var(--lp-r-3);
    display: flex;
    flex-direction: column;
    transition:
        border-color var(--lp-fast) var(--lp-ease),
        box-shadow var(--lp-fast) var(--lp-ease);

    &.is-focused {
        border-color: var(--lp-accent);
        box-shadow: 0 0 0 3px var(--lp-accent-wash);
    }

    .lp-composer__input {
        width: 100%;
        min-height: 20px;
        max-height: 220px;
        background: transparent;
        border: none;
        color: var(--lp-text);
        font-size: var(--lp-t-body);
        line-height: 1.55;
        resize: none;
        overflow-y: auto;

        &::placeholder {
            color: var(--lp-text-faint);
        }

        &:disabled {
            cursor: not-allowed;
        }
    }

    .lp-composer__foot {
        gap: 10px;
        display: flex;
        align-items: center;
        min-width: 0;
    }

    .lp-composer__hint {
        font-family: var(--lp-mono);
        font-size: var(--lp-t-sm);
        color: var(--lp-text-mute);
        white-space: nowrap;
    }

    .lp-composer__spacer {
        flex: 1;
    }

    .lp-composer__keys {
        gap: 4px;
        display: flex;
        align-items: center;
        flex-shrink: 0;
    }
}
</style>
