<template>
    <div class="value-input-editor">
        <div ref="editorContainer" class="value-input-editor-monaco"></div>
    </div>
</template>

<script>
import { markRaw } from 'vue'
import editorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker'
import jsonWorker from 'monaco-editor/esm/vs/language/json/json.worker?worker'
import cssWorker from 'monaco-editor/esm/vs/language/css/css.worker?worker'
import htmlWorker from 'monaco-editor/esm/vs/language/html/html.worker?worker'
import tsWorker from 'monaco-editor/esm/vs/language/typescript/ts.worker?worker'

export default {
    props: {
        modelValue: { default: '' },
        language: { default: 'plaintext' },
        lock: { default: true }
    },
    emits: ['update:modelValue'],
    data() {
        return {
            thisValue: this.normalizeValue(this.modelValue),
            monaco: null,
            editorInstance: null,
            changeDisposable: null
        }
    },
    watch: {
        modelValue(value) {
            const next = this.normalizeValue(value)
            if (next === this.thisValue) return
            this.thisValue = next
            this.updateEditorValue()
        },
        thisValue(value) {
            this.$emit('update:modelValue', value)
        },
        language() {
            this.updateEditorLanguage()
        },
        lock() {
            this.updateEditorOptions()
        }
    },
    async mounted() {
        await this.ensureEditor()
        this.updateEditorValue()
    },
    beforeUnmount() {
        this.disposeEditor()
    },
    methods: {
        ensureMonacoWorker() {
            if (window.MonacoEnvironment && window.MonacoEnvironment.getWorker) return
            window.MonacoEnvironment = {
                getWorker(_, label) {
                    if (label === 'json') return new jsonWorker()
                    if (label === 'css' || label === 'scss' || label === 'less')
                        return new cssWorker()
                    if (label === 'html' || label === 'handlebars' || label === 'razor')
                        return new htmlWorker()
                    if (label === 'typescript' || label === 'javascript') return new tsWorker()
                    return new editorWorker()
                }
            }
        },
        async ensureEditor() {
            if (this.editorInstance) return
            this.ensureMonacoWorker()
            const monaco = await import('monaco-editor')
            this.monaco = markRaw(monaco)
            this.editorInstance = markRaw(
                monaco.editor.create(this.$refs.editorContainer, {
                    value: this.thisValue,
                    language: this.normalizedLanguage,
                    readOnly: !this.lock,
                    automaticLayout: true,
                    minimap: { enabled: false },
                    scrollBeyondLastLine: false,
                    wordWrap: 'on',
                    lineNumbers: 'on',
                    glyphMargin: false,
                    folding: false,
                    lineDecorationsWidth: 8,
                    lineNumbersMinChars: 0,
                    renderLineHighlight: 'none',
                    overviewRulerBorder: false,
                    overviewRulerLanes: 0,
                    hideCursorInOverviewRuler: true,
                    theme: 'vs'
                })
            )
            this.changeDisposable = markRaw(
                this.editorInstance.onDidChangeModelContent(() => {
                    const value = this.editorInstance.getValue()
                    if (value === this.thisValue) return
                    this.thisValue = value
                })
            )
        },
        disposeEditor() {
            if (this.changeDisposable) {
                this.changeDisposable.dispose()
                this.changeDisposable = null
            }
            if (this.editorInstance) {
                this.editorInstance.dispose()
                this.editorInstance = null
            }
            this.monaco = null
        },
        updateEditorValue() {
            if (!this.editorInstance) return
            if (this.editorInstance.getValue() !== this.thisValue) {
                this.editorInstance.setValue(this.thisValue)
            }
        },
        updateEditorLanguage() {
            if (!this.editorInstance || !this.monaco) return
            const model = this.editorInstance.getModel()
            if (model) this.monaco.editor.setModelLanguage(model, this.normalizedLanguage)
        },
        updateEditorOptions() {
            if (!this.editorInstance) return
            this.editorInstance.updateOptions({ readOnly: !this.lock })
        },
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
        normalizeLanguage(language) {
            if (!language) return 'plaintext'
            const raw = String(language).toLowerCase()
            const aliasMap = {
                js: 'javascript',
                ts: 'typescript',
                json: 'json',
                py: 'python',
                sh: 'shell',
                bash: 'shell',
                zsh: 'shell',
                yml: 'yaml',
                md: 'markdown',
                html: 'html',
                vue: 'html',
                txt: 'plaintext',
                text: 'plaintext',
                plain: 'plaintext'
            }
            return aliasMap[raw] || 'plaintext'
        }
    },
    computed: {
        normalizedLanguage() {
            return this.normalizeLanguage(this.language)
        }
    }
}
</script>

<style lang="scss">
.value-input-editor {
    width: 100%;
}

.value-input-editor-monaco {
    width: 100%;
    min-height: 120px;
    height: 160px;
    border: 2px solid rgba(120, 120, 120, 0.15);
    border-radius: 6px;
    background: rgba(255, 255, 255, 0.9);
    box-sizing: border-box;
    overflow: hidden;
}
</style>
