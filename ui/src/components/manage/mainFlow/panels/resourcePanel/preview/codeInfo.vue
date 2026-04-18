<template>
    <div class="collapse-item-content">
        <div v-if="showBack" class="control-block">
            <fv-button
                background="transparent"
                border-radius="8"
                style="width: 30px; height: 30px"
                @click="$emit('back')"
            >
                <i class="ms-Icon ms-Icon--Back"></i>
            </fv-button>
            <p>{{ local('Back') }}</p>
        </div>
        <div ref="editorContainer" class="code-preview-wrapper"></div>
    </div>
</template>

<script>
import { mapState } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'
import editorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker'
import jsonWorker from 'monaco-editor/esm/vs/language/json/json.worker?worker'
import cssWorker from 'monaco-editor/esm/vs/language/css/css.worker?worker'
import htmlWorker from 'monaco-editor/esm/vs/language/html/html.worker?worker'
import tsWorker from 'monaco-editor/esm/vs/language/typescript/ts.worker?worker'

export default {
    props: {
        item: {
            type: Object,
            default: () => ({})
        },
        showBack: {
            type: Boolean,
            default: true
        }
    },
    data() {
        return {
            txtInfo: '',
            monaco: null,
            editorInstance: null,
            num_per_page: 60
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        fileExt() {
            if (!this.item || !this.item.id) return ''
            let path = this.item.id.split('?')[0]
            let fileName = path.split('/').pop() || ''
            if (!fileName.includes('.')) return ''
            return fileName.split('.').pop().toLowerCase()
        },
        codeLanguage() {
            const map = {
                yaml: 'yaml',
                yml: 'yaml',
                toml: 'ini',
                ini: 'ini',
                conf: 'ini',
                cfg: 'ini',
                json: 'json',
                jsonl: 'json',
                md: 'markdown',
                xml: 'xml',
                html: 'html',
                sh: 'shell',
                bash: 'shell',
                py: 'python',
                js: 'javascript',
                ts: 'typescript'
            }
            return map[this.fileExt] || 'plaintext'
        }
    },
    async mounted() {
        await this.ensureEditor()
        this.getData()
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
            this.monaco = monaco
            this.editorInstance = monaco.editor.create(this.$refs.editorContainer, {
                value: '',
                language: this.codeLanguage,
                readOnly: true,
                automaticLayout: true,
                minimap: { enabled: false },
                scrollBeyondLastLine: false,
                wordWrap: 'on',
                renderLineHighlight: 'none',
                theme: 'vs'
            })
        },
        disposeEditor() {
            if (!this.editorInstance) return
            this.editorInstance.dispose()
            this.editorInstance = null
            this.monaco = null
        },
        updateEditor() {
            if (!this.editorInstance || !this.monaco) return
            if (this.editorInstance.getValue() !== this.txtInfo) {
                this.editorInstance.setValue(this.txtInfo)
            }
            const model = this.editorInstance.getModel()
            if (model) {
                this.monaco.editor.setModelLanguage(model, this.codeLanguage)
            }
        },
        getData() {
            if (!this.item.id) return
            this.$api.resource.previewResource(this.item.id, 0, this.num_per_page).then((res) => {
                if (res.code === 200) {
                    const { samples } = res.data
                    this.txtInfo = samples.join('\n')
                    this.updateEditor()
                }
            })
        }
    }
}
</script>

<style lang="scss">
.control-block {
    position: relative;
    width: 100%;
    height: 35px;
    padding: 5px;
    gap: 5px;
    font-size: 13.8px;
    border-radius: 8px;
    display: flex;
    align-items: center;
    margin-bottom: 10px;
}

.code-preview-wrapper {
    position: relative;
    width: 100%;
    height: 500px;
    border: 1px solid rgba(120, 120, 120, 0.1);
    border-radius: 8px;
    overflow: hidden;
    background: white;
}
</style>
