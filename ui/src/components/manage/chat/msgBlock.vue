<template>
    <article class="lp-msg" :class="[`is-${thisValue.type}`]">
        <header class="lp-msg__head">
            <span v-if="thisValue.type !== 'user'" class="lp-msg__hue"></span>
            <span class="lp-label">{{ roleName }}</span>
            <div class="lp-msg__spacer"></div>
            <button
                v-if="thisValue.type !== 'tool'"
                type="button"
                class="lp-btn lp-btn--ghost lp-btn--icon lp-btn--sm lp-msg__copy"
                :title="local('Copy')"
                @click="copyText"
            >
                <svg v-if="!justCopied" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
                    <rect x="9" y="9" width="11" height="11" rx="2" />
                    <path d="M5 15V6a2 2 0 0 1 2-2h8" />
                </svg>
                <svg v-else width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M4 12l5 5L20 6" />
                </svg>
            </button>
        </header>

        <div v-if="thisValue.type !== 'tool'" v-html="mdHTML" class="lp-msg__body"></div>

        <div v-else class="lp-msg__tool">
            <div
                v-for="(item, index) in computedToolContent"
                :key="index"
                class="lp-msg__tool-row"
                :title="local('Copy')"
                @click="copyTextContent(item.value)"
            >
                <span class="lp-msg__tool-key">{{ item.key }}</span>
                <span class="lp-msg__tool-value">{{ item.value }}</span>
            </div>
        </div>
    </article>
</template>

<script>
import { mapState } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'
import { useTheme } from '@/stores/theme'

import MarkdownIt from 'markdown-it'
import katex from 'katex'
import markdownItTexMath from 'markdown-it-texmath'
import markdownItSubscript from 'markdown-it-sub'
import markdownItSuperscript from 'markdown-it-sup'
import markdownItMark from 'markdown-it-mark'
import hljs from 'highlight.js'
import 'highlight.js/styles/vs2015.css'

export default {
    name: 'MsgBlock',
    props: {
        modelValue: {
            type: Object,
            default: () => ({})
        },
        loadingMsg: {
            default: false
        }
    },
    data() {
        return {
            thisValue: this.modelValue,
            mdHTML: '',
            justCopied: false,
            md: new MarkdownIt({
                html: true,
                linkify: true,
                typographer: true,
                highlight: (code, lang) => {
                    if (lang && hljs.getLanguage(lang)) {
                        try {
                            return (
                                `<pre class="hljs"><code data-language="${lang}">` +
                                hljs.highlight(code, {
                                    language: lang,
                                    ignoreIllegals: true
                                }).value +
                                `</code></pre>`
                            )
                        } catch (error) {}
                    }
                    return ''
                }
            })
                .use(markdownItTexMath, {
                    engine: katex,
                    delimiters: 'dollars',
                    katexOptions: { throwOnError: false }
                })
                .use(markdownItSubscript)
                .use(markdownItSuperscript)
                .use(markdownItMark),
            timer: {
                copyIcon: null
            }
        }
    },
    watch: {
        modelValue(val) {
            this.thisValue = val
        },
        computedContent() {
            this.renderMarkdown()
        },
        loadingMsg() {
            this.renderMarkdown()
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        ...mapState(useTheme, ['color']),
        roleName() {
            if (this.thisValue.type === 'user') return this.local('You')
            if (this.thisValue.type === 'assistant') return this.local('Looper')
            if (!this.thisValue.type) return this.local('Looper')
            return this.thisValue.type[0].toUpperCase() + this.thisValue.type.slice(1)
        },
        computedContent() {
            try {
                return this.thisValue.data.content
            } catch (e) {}
            return ''
        },
        computedToolContent() {
            try {
                let content = JSON.parse(this.thisValue.data.content)
                let result = []
                for (let key in content) result.push({ key, value: content[key] })
                return result
            } catch (e) {}
            return []
        }
    },
    mounted() {
        this.renderMarkdown()
    },
    beforeUnmount() {
        clearTimeout(this.timer.copyIcon)
    },
    methods: {
        renderMarkdown() {
            let decode = this.computedContent.replace(/\n\n/g, '\n')
            decode = decode
                .replace(/\$\s*/g, '$')
                .replace(/\s*\$/g, '$')
                .replace(/\\\(\s*/g, '$')
                .replace(/\s*\\\)/g, '$')
                .replace(/\\\[\s*/g, '$$')
                .replace(/\s*\\\]/g, '$$')
            const mdHTML = this.md.render(decode)
            this.mdHTML = mdHTML
            if (this.loadingMsg) {
                // a caret riding the last line is the only "it is still writing" cue we need
                this.$nextTick(() => {
                    const contentEl = this.$el.querySelector('.lp-msg__body')
                    if (!contentEl) return
                    let last = contentEl.lastElementChild
                    if (last && ['UL', 'OL', 'PRE'].includes(last.nodeName)) {
                        last = last.lastElementChild
                    }
                    if (last) last.insertAdjacentHTML('beforeend', '<i class="lp-msg__caret"></i>')
                })
            }
        },
        copyText() {
            const content = this.computedContent.replace(/\n\n/g, '\n')
            navigator.clipboard.writeText(content).then(() => {
                this.justCopied = true
                clearTimeout(this.timer.copyIcon)
                this.timer.copyIcon = setTimeout(() => {
                    this.justCopied = false
                }, 1200)
            })
        },
        copyTextContent(text) {
            if (typeof text === 'object') text = JSON.stringify(text)
            navigator.clipboard.writeText(text)
        }
    }
}
</script>

<style lang="scss">
.lp-msg {
    position: relative;
    width: 100%;
    flex-shrink: 0;
    gap: 8px;
    display: flex;
    flex-direction: column;

    &:hover .lp-msg__copy {
        opacity: 1;
    }

    .lp-msg__head {
        gap: 7px;
        display: flex;
        align-items: center;
    }

    .lp-msg__hue {
        width: 3px;
        height: 12px;
        background: var(--lp-looper);
        border-radius: 2px;
        flex-shrink: 0;
    }

    .lp-msg__spacer {
        flex: 1;
    }

    .lp-msg__copy {
        opacity: 0;
        transition: opacity var(--lp-fast) var(--lp-ease);
    }

    .lp-msg__body {
        font-size: var(--lp-t-md);
        line-height: 1.65;
        color: var(--lp-text);
        overflow-wrap: anywhere;

        > *:not(:last-child) {
            margin-bottom: 8px;
        }

        h1,
        h2,
        h3,
        h4,
        h5 {
            margin-top: 4px;
            font-size: var(--lp-t-body);
            font-weight: 600;
        }

        ul,
        ol {
            padding-left: 18px;
        }

        li {
            margin: 2px 0;
        }

        code {
            padding: 1px 4px;
            background: var(--lp-raised);
            border-radius: var(--lp-r-1);
            font-size: var(--lp-t-sm);
        }

        pre {
            padding: 10px 12px;
            background: var(--lp-bg);
            border: 1px solid var(--lp-line);
            border-radius: var(--lp-r-2);
            font-size: var(--lp-t-sm);
            line-height: 1.7;
            overflow-x: auto;

            code {
                padding: 0;
                background: none;
            }
        }

        table {
            width: 100%;
            border-collapse: collapse;
            font-size: var(--lp-t-sm);

            th,
            td {
                padding: 5px 8px;
                border: 1px solid var(--lp-line);
                text-align: left;
            }

            th {
                background: var(--lp-raised);
            }
        }

        blockquote {
            padding-left: 10px;
            border-left: 2px solid var(--lp-line-hi);
            color: var(--lp-text-dim);
        }

        img {
            max-width: 100%;
        }
    }

    .lp-msg__caret {
        width: 6px;
        height: 12px;
        margin-left: 3px;
        background: var(--lp-accent);
        border-radius: 1px;
        display: inline-block;
        vertical-align: text-bottom;
        animation: lp-caret 1s steps(2, start) infinite;
    }

    .lp-msg__tool {
        padding: 8px 10px;
        gap: 4px;
        background: var(--lp-chrome);
        border: 1px solid var(--lp-line);
        border-radius: var(--lp-r-2);
        display: flex;
        flex-direction: column;
    }

    .lp-msg__tool-row {
        gap: 10px;
        font-family: var(--lp-mono);
        font-size: var(--lp-t-sm);
        line-height: 1.7;
        display: flex;
        cursor: copy;

        &:hover .lp-msg__tool-value {
            color: var(--lp-text);
        }
    }

    .lp-msg__tool-key {
        width: 84px;
        flex-shrink: 0;
        color: var(--lp-text-mute);
    }

    .lp-msg__tool-value {
        flex: 1;
        min-width: 0;
        color: var(--lp-text-dim);
        overflow-wrap: anywhere;
    }

    /* the user's own turn reads as an inset card, the agent's as plain text */
    &.is-user .lp-msg__body {
        padding: 9px 11px;
        background: var(--lp-surface);
        border-radius: var(--lp-r-2);
    }
}

@keyframes lp-caret {
    to {
        opacity: 0.15;
    }
}
</style>
