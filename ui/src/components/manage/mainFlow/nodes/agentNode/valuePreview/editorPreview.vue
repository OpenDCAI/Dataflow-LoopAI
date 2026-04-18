<template>
    <div class="value-preview-code">
        <pre class="value-preview-code-block"><code v-html="highlightedHtml"></code></pre>
    </div>
</template>

<script>
import hljs from 'highlight.js/lib/core'
import 'highlight.js/styles/github.css'

import javascript from 'highlight.js/lib/languages/javascript'
import typescript from 'highlight.js/lib/languages/typescript'
import json from 'highlight.js/lib/languages/json'
import python from 'highlight.js/lib/languages/python'
import java from 'highlight.js/lib/languages/java'
import cpp from 'highlight.js/lib/languages/cpp'
import csharp from 'highlight.js/lib/languages/csharp'
import go from 'highlight.js/lib/languages/go'
import xml from 'highlight.js/lib/languages/xml'
import bash from 'highlight.js/lib/languages/bash'
import markdown from 'highlight.js/lib/languages/markdown'
import yaml from 'highlight.js/lib/languages/yaml'
import sql from 'highlight.js/lib/languages/sql'
import rust from 'highlight.js/lib/languages/rust'
import php from 'highlight.js/lib/languages/php'

hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('json', json)
hljs.registerLanguage('python', python)
hljs.registerLanguage('java', java)
hljs.registerLanguage('cpp', cpp)
hljs.registerLanguage('csharp', csharp)
hljs.registerLanguage('go', go)
hljs.registerLanguage('xml', xml)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('markdown', markdown)
hljs.registerLanguage('yaml', yaml)
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('rust', rust)
hljs.registerLanguage('php', php)

export default {
    props: {
        modelValue: {
            default: ''
        },
        language: {
            default: 'plaintext'
        }
    },
    computed: {
        normalizedContent() {
            return this.normalizeValue(this.modelValue)
        },
        normalizedLanguage() {
            return this.normalizeLanguage(this.language)
        },
        highlightedHtml() {
            const code = this.normalizedContent
            if (!code) return ''

            if (!this.normalizedLanguage) return this.escapeHtml(code)

            if (hljs.getLanguage(this.normalizedLanguage)) {
                return hljs.highlight(code, {
                    language: this.normalizedLanguage,
                    ignoreIllegals: true
                }).value
            }

            return this.escapeHtml(code)
        }
    },
    methods: {
        normalizeValue(value) {
            if (value === null || value === undefined) return ''
            if (typeof value === 'string') return value
            if (typeof value === 'number' || typeof value === 'boolean') return String(value)
            try {
                return JSON.stringify(value, null, 2)
            } catch (error) {
                return String(value)
            }
        },
        normalizeLanguage(lang) {
            if (!lang) return null
            const raw = String(lang).toLowerCase()
            const aliasMap = {
                js: 'javascript',
                ts: 'typescript',
                py: 'python',
                sh: 'bash',
                shell: 'bash',
                zsh: 'bash',
                yml: 'yaml',
                md: 'markdown',
                html: 'xml',
                vue: 'xml',
                'c++': 'cpp',
                'c#': 'csharp',
                txt: null,
                text: null,
                plain: null,
                plaintext: null
            }
            return Object.prototype.hasOwnProperty.call(aliasMap, raw) ? aliasMap[raw] : raw
        },
        escapeHtml(str) {
            return str
                .replaceAll('&', '&amp;')
                .replaceAll('<', '&lt;')
                .replaceAll('>', '&gt;')
                .replaceAll('"', '&quot;')
                .replaceAll("'", '&#39;')
        }
    }
}
</script>

<style lang="scss">
.value-preview-code {
    width: 100%;
    min-height: 120px;
    max-height: 220px;
    border: 1px solid rgba(120, 120, 120, 0.15);
    border-radius: 6px;
    background: rgba(255, 255, 255, 0.9);
    overflow: auto;
}

.value-preview-code-block {
    margin: 0;
    padding: 10px;
    font-size: 12px;
    line-height: 1.5;
    white-space: pre-wrap;
    word-break: break-word;
    overflow-wrap: anywhere;
}
</style>
