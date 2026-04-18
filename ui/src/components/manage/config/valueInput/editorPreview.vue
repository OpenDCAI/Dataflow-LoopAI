<template>
    <div class="value-preview-editor">
        <div ref="editorContainer" class="value-preview-editor-container"></div>
    </div>
</template>

<script>
import * as monaco from 'monaco-editor'

export default {
    props: {
        modelValue: {
            default: ''
        },
        language: {
            default: 'plaintext'
        }
    },
    data() {
        return {
            editorInstance: null
        }
    },
    watch: {
        modelValue(value) {
            if (!this.editorInstance) return
            const next = this.normalizeValue(value)
            if (this.editorInstance.getValue() !== next) {
                this.editorInstance.setValue(next)
            }
        },
        language(value) {
            if (!this.editorInstance) return
            const model = this.editorInstance.getModel()
            if (model) {
                monaco.editor.setModelLanguage(model, value || 'plaintext')
            }
        }
    },
    mounted() {
        this.editorInstance = monaco.editor.create(this.$refs.editorContainer, {
            value: this.normalizeValue(this.modelValue),
            language: this.language || 'plaintext',
            readOnly: true,
            automaticLayout: true,
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            wordWrap: 'on',
            lineNumbersMinChars: 3,
            renderLineHighlight: 'none',
            theme: 'vs'
        })
    },
    beforeUnmount() {
        if (this.editorInstance) {
            this.editorInstance.dispose()
            this.editorInstance = null
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
        }
    }
}
</script>

<style lang="scss">
.value-preview-editor {
    width: 100%;
    min-height: 120px;
    height: 160px;
    border: 1px solid rgba(120, 120, 120, 0.15);
    border-radius: 6px;
    overflow: hidden;
    background: rgba(255, 255, 255, 0.9);
}

.value-preview-editor-container {
    width: 100%;
    height: 100%;
}
</style>
