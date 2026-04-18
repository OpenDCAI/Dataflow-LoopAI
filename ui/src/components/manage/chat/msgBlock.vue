<template>
    <div class="msg-block" :class="[{ dark: theme === 'dark' }]">
        <div class="msg-wrapper">
            <div class="msg-role-block">
                <p class="msg-guid">
                    {{ getRoleName }}
                </p>
            </div>
            <div class="msg-content-block">
                <div
                    v-if="thisValue.type === 'human'"
                    class="msg-role-block"
                    :style="{ background: gradient }"
                >
                    <img class="agent-logo" :src="img.user" draggable="false" alt="" />
                </div>
                <div
                    v-if="thisValue.type === 'ai'"
                    class="msg-role-block"
                    :style="{ background: 'rgba(245, 245, 245, 1)' }"
                >
                    <img class="agent-logo" :src="img.agent" draggable="false" alt="" />
                </div>
                <div
                    v-if="thisValue.type === 'tool'"
                    class="msg-role-block"
                    :style="{ background: 'rgba(245, 245, 245, 1)' }"
                >
                    <img class="agent-logo" :src="img.tool" draggable="false" alt="" />
                </div>
                <div
                    v-if="!editable && thisValue.type !== 'tool'"
                    v-html="mdHTML"
                    class="msg-content"
                ></div>
                <div
                    v-if="editable && thisValue.type !== 'tool'"
                    class="msg-editable-content-block"
                >
                    <power-editor
                        :placeholder="local('Edit your question...')"
                        :theme="theme"
                        class="msg-power-editor"
                        ref="editor"
                        :editorBackground="theme === 'dark' ? 'rgba(52, 64, 84, 0.3)' : 'white'"
                        :editorOutSideBackground="
                            theme === 'dark' ? 'rgba(52, 64, 84, 0.3)' : 'white'
                        "
                        @on-mounted="setEditorContent"
                    ></power-editor>
                    <div class="msg-editable-control-block">
                        <fv-button
                            theme="dark"
                            :background="gradient"
                            :is-box-shadow="true"
                            :disabled="holdon"
                            style="width: 120px"
                            @click="getEditorContent"
                            >{{ local('Submit') }}</fv-button
                        >
                        <fv-button
                            :is-box-shadow="true"
                            style="margin-left: 15px; width: 120px"
                            @click="editable = false"
                            >{{ local('Cancel') }}</fv-button
                        >
                    </div>
                </div>
                <div v-if="!editable && thisValue.type === 'tool'" class="tool-msg-info">
                    <span
                        v-for="(item, index) in computedToolContent"
                        class="tool-msg-item"
                        :class="[{ long: computeLength(item.value) > 20 }]"
                        :key="index"
                        @click="copyTextContent(item.value)"
                    >
                        <p class="tool-msg-key">{{ item.key }}</p>
                        <p class="tool-msg-value">{{ item.value }}</p>
                    </span>
                </div>
            </div>
            <div v-show="thisValue.type !== 'tool'" class="msg-control-block">
                <div class="msg-control-right-block">
                    <fv-button
                        v-show="thisValue.type === 'human'"
                        :theme="theme"
                        :background="
                            theme === 'dark' ? 'rgba(50, 58, 71, 1)' : 'rgba(255, 255, 255, 1)'
                        "
                        :border-radius="50"
                        style="width: 30px; height: 30px; flex-shrink: 0"
                        @click="editable = !editable"
                    >
                        <i
                            class="ms-Icon"
                            :class="[`ms-Icon--${editable ? 'Accept' : 'Edit'}`]"
                            style="font-size: 12px"
                        ></i>
                    </fv-button>
                    <fv-button
                        :border-radius="50"
                        :theme="theme"
                        :background="
                            theme === 'dark' ? 'rgba(50, 58, 71, 1)' : 'rgba(255, 255, 255, 1)'
                        "
                        style="width: 30px; height: 30px; margin-left: 5px; flex-shrink: 0"
                        :title="local('Copy')"
                        @click="copyText"
                    >
                        <i
                            class="ms-Icon"
                            :class="[`ms-Icon--${copyIcon}`]"
                            style="font-size: 12px"
                        ></i>
                    </fv-button>
                </div>
            </div>
        </div>
    </div>
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

import loopAI from '@/assets/logo/LoopAI_logo.svg'
import userImg from '@/assets/chat/user.svg'
import toolImg from '@/assets/chat/tool.svg'

export default {
    props: {
        modelValue: {
            type: Object,
            default: () => {}
        },
        holdon: {
            default: false
        },
        loadingMsg: {
            default: false
        },
        theme: {
            default: 'light'
        }
    },
    data() {
        return {
            thisValue: this.modelValue,
            mdHTML: '',
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
                    return '' // 如果无法识别语言，则返回原始代码
                }
            })
                .use(markdownItTexMath, {
                    engine: katex,
                    delimiters: 'dollars', // 支持 \(...\), \[...\], $$...$$
                    katexOptions: { throwOnError: false }
                })
                .use(markdownItSubscript)
                .use(markdownItSuperscript)
                .use(markdownItMark),
            copyIcon: 'Set',
            img: {
                agent: loopAI,
                user: userImg,
                tool: toolImg
            },
            editable: false,
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
        ...mapState(useTheme, ['color', 'gradient']),
        getRoleName() {
            if (this.thisValue.type === 'human') return this.local('You')
            if (this.thisValue.type === 'ai') return this.local('AI')
            return this.thisValue.type[0].toUpperCase() + this.thisValue.type.slice(1)
        },
        computedContent() {
            try {
                let content = this.thisValue.data.content
                return content
            } catch (e) {}

            return ''
        },
        computedToolContent() {
            try {
                let content = this.thisValue.data.content
                content = JSON.parse(content)
                let result = []
                for (let key in content)
                    result.push({
                        key,
                        value: content[key]
                    })
                return result
            } catch (e) {}

            return []
        }
    },
    mounted() {
        this.renderMarkdown()
    },
    methods: {
        renderMarkdown() {
            let decode = this.computedContent.replace(/\n\n/g, '\n')
            decode = decode
                .replace(/\$\s*/g, '$') // $ 后空格
                .replace(/\s*\$/g, '$') // $ 前空格
                .replace(/\\\(\s*/g, '$') // \( 后空格
                .replace(/\s*\\\)/g, '$') // \) 前空格
                .replace(/\\\[\s*/g, '$$') // \[ 后空格
                .replace(/\s*\\\]/g, '$$') // \] 前空格
            let mdHTML = this.md.render(decode)
            this.mdHTML = mdHTML
            if (this.loadingMsg) {
                this.$nextTick(() => {
                    let contentEl = this.$el.querySelector('.msg-content')
                    let last = contentEl.lastElementChild
                    if (last && ['UL', 'OL', 'PRE'].includes(last.nodeName)) {
                        last = last.lastElementChild
                    }
                    if (last) {
                        let rangeEl = `<i class="msg-content-generating-block" style="background: ${this.gradient};"></i>`
                        last.insertAdjacentHTML('beforeend', rangeEl)
                    }
                })
            } else if (!this.loadingMsg) {
                this.mdHTML = mdHTML + '<i></i>'
            }
        },
        setEditorContent() {
            let decode = this.computedContent.replace(/\n\n/g, '\n')
            return this.$refs.editor.insertMarkdown(decode)
        },
        getEditorContent() {
            let content = this.$refs.editor.saveMarkdown()
            this.$emit('revise-submit', {
                msg: this.thisValue,
                content
            })
            this.editable = false
        },
        copyText() {
            let content = this.computedContent.replace(/\n\n/g, '\n')
            navigator.clipboard.writeText(content).then(() => {
                this.copyIcon = 'Accept'
                clearTimeout(this.timer.copyIcon)
                this.timer.copyIcon = setTimeout(() => {
                    this.copyIcon = 'Set'
                }, 1000)
            })
        },
        copyTextContent (text) {
            if(typeof(text) === 'object') text = JSON.stringify(text);
            navigator.clipboard.writeText(text).then(() => {
                this.$barWarning(this.local('Copied'), {
                    status: "correct"
                })
            })
        },
        computeLength(obj) {
            if (!obj) return 0
            if (typeof obj === 'string') return obj.length
            try {
                obj = JSON.stringify(obj)
                return obj.length
            } catch (e) {
                return 0
            }
        }
    }
}
</script>

<style lang="scss">
.msg-block {
    @include HcenterC;

    position: relative;
    width: 100%;
    height: auto;
    flex-shrink: 0;
    margin-bottom: 5px;
    display: flex;
    overflow: hidden;

    &:last-child {
        margin-bottom: 100px;
    }

    &.dark {
        .msg-role-block {
            .msg-guid {
                color: whitesmoke;
            }
        }

        .msg-wrapper {
            background: rgba(43, 50, 76, 0.6);
        }

        .msg-content-block {
            .msg-content {
                color: rgba(185, 188, 200, 1);

                a {
                    color: rgba(155, 155, 255, 1);
                }
            }
        }

        .msg-control-block {
            .version-display-block {
                color: whitesmoke;
            }
        }
    }

    .msg-wrapper {
        @include HcenterC;

        position: relative;
        width: 100%;
        max-width: 900px;
        height: auto;
        padding: 10px 0px;
        background: rgba(255, 255, 255, 0.3);
        border: rgba(160, 160, 160, 0.2) solid thin;
        border-radius: 12px;
        transition: background 0.3s;
        display: flex;
        backdrop-filter: blur(10px);
        overflow: hidden;

        &:hover {
            background: rgba(255, 255, 255, 0.8);
        }
    }

    .msg-role-block {
        @include Vcenter;

        position: relative;
        width: 100%;
        max-width: 900px;
        height: auto;
        flex-shrink: 0;
        padding: 5px 15px;
        border-radius: 8px;

        .msg-guid {
            @include nowrap;

            font-size: 13px;
            font-weight: bold;
            color: rgba(13, 13, 13, 1);
            user-select: none;
        }
    }

    .msg-control-block {
        @include Vstart;

        position: relative;
        width: 100%;
        max-width: 900px;
        height: auto;
        flex-shrink: 0;
        padding: 5px 15px;
        border-radius: 8px;

        .msg-control-left-block {
            @include Vcenter;

            width: 150px;
            height: 30px;
            flex-shrink: 0;

            .version-display-block {
                @include HcenterVcenter;

                margin: 0px 25px;
                font-size: 12px;
            }
        }

        .msg-control-right-block {
            @include Hend;

            width: auto;
            height: auto;
            flex: 1;
            overflow: hidden;
        }
    }

    .msg-content-block {
        position: relative;
        width: 100%;
        max-width: 900px;
        height: auto;
        flex-shrink: 0;
        padding: 5px 15px;
        display: flex;

        .msg-role-block {
            @include HcenterVcenter;

            width: 35px;
            height: 35px;
            border-radius: 50%;
            user-select: none;
            overflow: hidden;

            .agent-logo {
                width: 20px;
                height: 20px;
            }

            .model-avatar {
                width: 35px;
                height: 35px;
                object-fit: cover;
            }
        }

        .msg-content {
            @include VcenterC;

            position: relative;
            width: 10px;
            flex: 1;
            padding: 0px 15px;
            font-size: 0.8rem;
            color: rgba(55, 65, 81, 1);
            line-height: 1.6;

            * {
                max-width: 100%;
                line-height: 1.5;
            }

            p {
                white-space: pre-wrap;
            }

            li {
                margin-left: 15px;
                margin-top: 10px;
            }

            pre {
                width: 100%;
                margin: 15px 0px;
                padding: 15px;
                background-color: rgba(36, 36, 36, 1);
                border-radius: 8px;
                box-sizing: border-box;
                line-height: 1;
                overflow-x: overlay;

                code {
                    color: inherit;
                    padding: 0;
                    background: none;
                    font-family: Consolas, Monaco, 'Andale Mono', 'Ubuntu Mono', monospace;
                    color: whitesmoke;
                    line-height: 1.5;

                    &::before {
                        content: attr(data-language);

                        margin-bottom: 10px;
                        color: rgba(245, 245, 245, 0.6);
                        display: block;
                    }
                }
            }

            code {
                padding: 4px 6px;
                background-color: rgba(#616161, 0.1);
                font-family: Consolas, Monaco, 'Andale Mono', 'Ubuntu Mono', monospace;
                font-size: 0.8rem;
                color: rgba(235, 87, 87, 1);
                border-radius: 3px;
            }

            table {
                border-collapse: collapse;
                table-layout: fixed;
                width: 100%;
                margin: 5px 0px;
                display: table;
                overflow: hidden;
                overflow-y: visible;

                td,
                th {
                    min-width: 1em;
                    border: thin solid #ced4da;
                    padding: 3px 5px;
                    vertical-align: top;
                    box-sizing: border-box;
                    position: relative;

                    > * {
                        margin-bottom: 0;
                    }
                }

                th {
                    width: auto;
                    font-weight: bold;
                    text-align: left;
                    background-color: rgba(241, 243, 245, 1);
                }

                .selectedCell:after {
                    z-index: 2;
                    position: absolute;
                    content: '';
                    left: 0;
                    right: 0;
                    top: 0;
                    bottom: 0;
                    background: rgba(200, 200, 255, 0.4);
                    pointer-events: none;
                }

                .column-resize-handle {
                    position: absolute;
                    right: -2px;
                    top: 0;
                    bottom: -2px;
                    width: 4px;
                    background-color: rgba(145, 191, 209, 1);
                    pointer-events: none;
                }

                p {
                    margin: 0;
                }
            }

            .msg-content-generating-block {
                position: relative;
                top: 2px;
                width: 16px;
                height: 16px;
                margin-left: 10px;
                border-radius: 50%;
                user-select: none;
                animation: flash 0.1s infinite alternate;
                display: inline-block;
            }

            @keyframes flash {
                from {
                    opacity: 0;
                }

                to {
                    opacity: 1;
                    transform: scale(1.2);
                }
            }
        }

        .msg-editable-content-block {
            @include HcenterC;

            flex: 1;
            margin-left: 15px;

            .msg-power-editor {
                width: 100%;
                height: 300px;
                border: rgba(200, 200, 200, 0.1) solid thin;
                overflow: hidden;
            }

            .msg-editable-control-block {
                @include HcenterVcenter;

                width: 100%;
                margin-top: 15px;
            }
        }

        .tool-msg-info {
            @include Vcenter;

            width: 1px;
            max-width: 100%;
            padding-left: 15px;
            gap: 5px;
            flex: 1;
            flex-wrap: wrap;
            user-select: none;
            cursor: default;

            .tool-msg-item {
                @include HbetweenVcenter;

                width: auto;
                max-width: 100%;
                height: auto;
                gap: 5px;
                padding: 5px;
                background: linear-gradient(128deg, rgba(95, 75, 189, 1), rgba(148, 136, 225, 1));
                font-size: 12px;
                color: whitesmoke;
                font-weight: bold;
                border-radius: 999px;
                box-shadow: 0px 1px 1px rgba(0, 0, 0, 0.1);

                &.long {
                    border-radius: 8px;
                    flex-direction: column;
                    overflow-x: overlay;

                    .tool-msg-value {
                        border-radius: 8px;
                    }
                }

                .tool-msg-key {
                    @include Vcenter;

                    height: 100%;
                }

                .tool-msg-value {
                    @include Vcenter;

                    width: auto;
                    max-width: 100%;
                    min-height: 35px;
                    flex: 1;
                    height: auto;
                    flex-shrink: 0;
                    padding: 5px 10px;
                    background: rgba(255, 255, 255, 1);
                    font-size: 12px;
                    color: rgba(95, 75, 189, 1);
                    border-radius: 999px;
                    white-space: pre-wrap;
                    text-overflow: ellipsis;
                    overflow-x: overlay;
                }
            }
        }
    }
}

@media screen and (max-width: 1024px) {
    .msg-block {
        .msg-control-block {
            .msg-control-right-block {
            }
        }
    }
}
</style>
