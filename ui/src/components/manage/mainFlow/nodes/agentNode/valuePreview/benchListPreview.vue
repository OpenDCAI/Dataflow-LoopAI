<template>
    <div class="bench-list-preview">
        <div v-if="!benchItems.length" class="bench-list-empty">
            {{ local('No benches configured.') }}
        </div>

        <div
            v-for="(bench, index) in benchItems"
            :key="benchKey(bench, index)"
            class="bench-card"
        >
            <div class="bench-card-header">
                <div>
                    <p class="bench-card-title">
                        {{ bench.name || `${local('Bench')} ${index + 1}` }}
                    </p>
                    <p class="bench-card-subtitle">{{ buildBenchPath(index) }}</p>
                </div>
            </div>

            <div class="bench-grid">
                <div class="bench-field full">
                    <p class="bench-field-label" style="color: #000">
                        {{ local('name') }}
                    </p>
                    <fv-text-box
                        :model-value="bench.name || ''"
                        :placeholder="local('name')"
                        border-radius="6"
                        :border-width="2"
                        :reveal-border="true"
                        :border-color="'rgba(120, 120, 120, 0.1)'"
                        :focus-border-color="foreground"
                        :is-box-shadow="true"
                        underline
                        style="width: 100%"
                        readonly
                    ></fv-text-box>
                </div>

                <div class="bench-field">
                    <p class="bench-field-label" style="color: #000">
                        {{ local('task_type') }}
                    </p>
                    <fv-combobox
                        :model-value="selectOption(bench.task_type, taskTypeOptions)"
                        :options="taskTypeOptions"
                        style="width: 100%"
                        :disabled="true"
                    ></fv-combobox>
                </div>

                <div class="bench-field">
                    <p class="bench-field-label" style="color: #000">
                        {{ local('eval_type') }}
                    </p>
                    <fv-combobox
                        :model-value="selectOption(bench.eval_type, evalTypeOptions)"
                        :options="evalTypeOptions"
                        style="width: 100%"
                        :disabled="true"
                    ></fv-combobox>
                </div>

                <div class="bench-field full">
                    <p class="bench-field-label" style="color: #000">
                        {{ local('problem_path') }}
                    </p>
                    <div class="bench-path-row">
                        <fv-breadcrumb
                            v-model="bench.problem_path"
                            class="bench-breadcrumb"
                            :root-icon="'View'"
                            :border-radius="6"
                            :font-size="'10px'"
                            :disabled="true"
                            :title="bench.problem_path"
                            @click="openPathDialog(buildBenchPath(index), 'problem_path')"
                        ></fv-breadcrumb>
                        <component
                            :is="pathComponent(bench.problem_path)"
                            v-model="dialogState[dialogKey(buildBenchPath(index), 'problem_path')]"
                            v-model:filePath="bench.problem_path"
                            :readOnly="true"
                        ></component>
                    </div>
                </div>

                <div v-if="bench.task_type === 'text2sql'" class="bench-field full">
                    <p class="bench-field-label" style="color: #000">
                        {{ local('text2sql_dir') }}
                    </p>
                    <div class="bench-path-row">
                        <fv-breadcrumb
                            v-model="bench.text2sql_dir"
                            class="bench-breadcrumb"
                            :root-icon="'View'"
                            :border-radius="6"
                            :font-size="'10px'"
                            :disabled="true"
                            :title="bench.text2sql_dir"
                            @click="openPathDialog(buildBenchPath(index), 'text2sql_dir')"
                        ></fv-breadcrumb>
                        <component
                            :is="pathComponent(bench.text2sql_dir)"
                            v-model="dialogState[dialogKey(buildBenchPath(index), 'text2sql_dir')]"
                            v-model:filePath="bench.text2sql_dir"
                            :readOnly="true"
                        ></component>
                    </div>
                </div>

                <div class="bench-field">
                    <p class="bench-field-label" style="color: #000">
                        {{ local('case_num') }}
                    </p>
                    <fv-text-box
                        :model-value="formatNumber(bench.case_num)"
                        :placeholder="local('case_num')"
                        border-radius="6"
                        :border-width="2"
                        :reveal-border="true"
                        :border-color="'rgba(120, 120, 120, 0.1)'"
                        :focus-border-color="foreground"
                        :is-box-shadow="true"
                        underline
                        style="width: 100%"
                        readonly
                    ></fv-text-box>
                </div>

                <div class="bench-field">
                    <p class="bench-field-label" style="color: #000">
                        {{ local('batch_size') }}
                    </p>
                    <fv-text-box
                        :model-value="formatNumber(bench.batch_size)"
                        :placeholder="local('batch_size')"
                        border-radius="6"
                        :border-width="2"
                        :reveal-border="true"
                        :border-color="'rgba(120, 120, 120, 0.1)'"
                        :focus-border-color="foreground"
                        :is-box-shadow="true"
                        underline
                        style="width: 100%"
                        readonly
                    ></fv-text-box>
                </div>

                <div class="bench-field full">
                    <p class="bench-field-label" style="color: #000">
                        {{ local('key_mapping') }}
                    </p>
                    <editor-preview
                        :model-value="jsonText(bench.key_mapping)"
                        language="json"
                    ></editor-preview>
                </div>
            </div>

            <div class="secondary-section">
                <div class="secondary-section-header">
                    <p class="bench-field-label" style="color: #000">
                        {{ local('secondary_benches') }}
                    </p>
                </div>
                <bench-list-preview
                    :model-value="bench.secondary_benches"
                    :nested-allowed-values="nestedAllowedValues"
                    :path-prefix="`${buildBenchPath(index)}.secondary_benches`"
                    :foreground="foreground"
                ></bench-list-preview>
            </div>
        </div>
    </div>
</template>

<script>
import { mapState } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'

import directorySelector from '@/components/general/directorySelector.vue'
import resPreviewPanel from './resPreviewPanel.vue'
import editorPreview from './editorPreview.vue'

export default {
    name: 'BenchListPreview',
    components: {
        directorySelector,
        resPreviewPanel,
        editorPreview
    },
    props: {
        modelValue: { default: () => [] },
        nestedAllowedValues: { default: null },
        pathPrefix: { default: 'bench_list' },
        foreground: { default: '' }
    },
    data() {
        return {
            dialogState: {},
            benchUid: 0,
            benchUidMap: new WeakMap()
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        benchItems() {
            if (!Array.isArray(this.modelValue)) return []
            this.modelValue.forEach((bench) => this.normalizeBench(bench))
            return this.modelValue
        },
        nestedOptions() {
            return this.nestedAllowedValues || {}
        },
        taskTypeOptions() {
            return this.formatOptions(
                this.nestedOptions.task_type || ['code', 'text2sql', 'general_text']
            )
        },
        evalTypeOptions() {
            return this.formatOptions(
                this.nestedOptions.eval_type || ['key2_qa', 'key1_text_score']
            )
        }
    },
    methods: {
        normalizeBench(bench) {
            if (!bench || typeof bench !== 'object') return
            if (!Array.isArray(bench.secondary_benches)) bench.secondary_benches = []
            if (!Object.prototype.hasOwnProperty.call(bench, 'name')) bench.name = ''
            if (!Object.prototype.hasOwnProperty.call(bench, 'task_type')) {
                bench.task_type = this.taskTypeOptions[0]?.key || ''
            }
            if (!Object.prototype.hasOwnProperty.call(bench, 'eval_type')) {
                bench.eval_type = this.evalTypeOptions[0]?.key || ''
            }
            if (!Object.prototype.hasOwnProperty.call(bench, 'problem_path')) bench.problem_path = ''
            if (!Object.prototype.hasOwnProperty.call(bench, 'text2sql_dir')) {
                bench.text2sql_dir = ''
            }
            if (!Object.prototype.hasOwnProperty.call(bench, 'case_num')) bench.case_num = null
            if (!Object.prototype.hasOwnProperty.call(bench, 'batch_size')) bench.batch_size = null
            if (!Object.prototype.hasOwnProperty.call(bench, 'key_mapping')) bench.key_mapping = {}
        },
        formatOptions(list) {
            return (list || []).map((item) => ({
                key: item,
                text: item
            }))
        },
        selectOption(value, options) {
            return options.find((item) => item.key === value) || {}
        },
        benchKey(bench, index) {
            if (!bench || typeof bench !== 'object') return `${this.pathPrefix}-${index}`
            if (!this.benchUidMap.has(bench)) {
                this.benchUid += 1
                this.benchUidMap.set(bench, `${this.pathPrefix}-${this.benchUid}`)
            }
            return this.benchUidMap.get(bench)
        },
        buildBenchPath(index) {
            return `${this.pathPrefix}[${index}]`
        },
        formatNumber(value) {
            if (value === null || value === undefined) return ''
            return String(value)
        },
        jsonText(value) {
            if (value === null || value === undefined || value === '') return '{}'
            if (typeof value === 'string') return value
            try {
                return JSON.stringify(value, null, 2)
            } catch (error) {
                return '{}'
            }
        },
        dialogKey(path, field) {
            return `${path}.${field}`
        },
        openPathDialog(path, field) {
            this.dialogState[this.dialogKey(path, field)] = true
        },
        pathComponent(path) {
            const allowedExts = [
                'csv',
                'tsv',
                'txt',
                'md',
                'json',
                'jsonl',
                'html',
                'yaml',
                'yml',
                'toml',
                'ini',
                'cfg',
                'conf'
            ]
            if (!path) return directorySelector
            let fileName = path.split('/').pop() || ''
            let fileExt = fileName.split('.').pop().toLowerCase()
            if (allowedExts.includes(fileExt)) return resPreviewPanel
            return directorySelector
        }
    }
}
</script>

<style lang="scss">
.bench-list-preview {
    width: 100%;
    display: flex;
    flex-direction: column;
    gap: 12px;

    .bench-list-empty {
        padding: 16px;
        border-radius: 10px;
        background: rgba(255, 255, 255, 0.8);
        border: 1px dashed rgba(120, 120, 120, 0.2);
        font-size: 12px;
        color: rgba(120, 120, 120, 1);
    }

    .bench-card {
        padding: 14px;
        border-radius: 14px;
        background: rgba(255, 255, 255, 0.82);
        border: rgba(120, 120, 120, 0.08) solid thin;
        box-shadow: 0px 6px 18px rgba(18, 26, 64, 0.05);
    }

    .bench-card-header {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 12px;
        margin-bottom: 12px;
    }

    .bench-card-title {
        font-size: 14px;
        font-weight: 600;
        color: rgba(35, 41, 70, 1);
    }

    .bench-card-subtitle {
        margin-top: 2px;
        font-size: 11px;
        color: rgba(125, 132, 156, 1);
    }

    .bench-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        gap: 12px;
    }

    .bench-field {
        min-width: 0;

        &.full {
            grid-column: 1 / -1;
        }
    }

    .bench-field-label {
        margin-bottom: 6px;
        font-size: 12px;
        color: rgba(95, 95, 95, 1);
    }

    .bench-path-row {
        display: flex;
        align-items: center;
        gap: 6px;
    }

    .bench-breadcrumb {
        flex: 1;
        flex-shrink: 0;
        height: 30px;
        background: rgba(255, 255, 255, 0.3);
        border: rgba(199, 168, 252, 0) solid 2px;
        box-sizing: border-box;
        transition: all 0.3s;
        cursor: pointer;

        &:hover {
            background: rgba(255, 255, 255, 0.9);
            border: rgba(199, 168, 252, 0.3) solid 2px;
        }
    }

    .secondary-section {
        margin-top: 16px;
        padding-top: 12px;
        border-top: rgba(120, 120, 120, 0.1) solid thin;
    }

    .secondary-section-header {
        margin-bottom: 10px;
    }
}

@media (max-width: 520px) {
    .bench-list-preview {
        .bench-grid {
            grid-template-columns: 1fr;
        }
    }
}
</style>
