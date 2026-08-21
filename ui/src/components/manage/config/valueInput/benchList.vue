<template>
    <div class="bench-list-input">
        <div class="bench-list-toolbar">
            <p class="bench-list-summary">
                {{ local('Bench Count') }}: <span>{{ benchItems.length }}</span>
            </p>
            <fv-button
                theme="dark"
                :background="color"
                border-radius="8"
                style="height: 30px; min-width: 104px"
                :disabled="!lock"
                @click="addBench"
            >
                {{ local('Add Bench') }}
            </fv-button>
        </div>

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
                <fv-button
                    theme="dark"
                    background="rgba(200, 38, 45, 1)"
                    border-radius="30"
                    style="width: 28px; height: 28px; flex-shrink: 0"
                    :disabled="!lock"
                    :title="local('Remove Bench')"
                    @click="removeBench(index)"
                >
                    <i class="ms-Icon ms-Icon--Delete"></i>
                </fv-button>
            </div>

            <div class="bench-grid">
                <div class="bench-field full">
                    <p class="bench-field-label">{{ local('name') }}</p>
                    <fv-text-box
                        v-model="bench.name"
                        :placeholder="local('name')"
                        border-radius="6"
                        :border-width="2"
                        :reveal-border="true"
                        :disabled="!lock"
                        :border-color="'rgba(120, 120, 120, 0.1)'"
                        :focus-border-color="color"
                        :is-box-shadow="true"
                        underline
                    ></fv-text-box>
                </div>

                <div class="bench-field">
                    <p class="bench-field-label">{{ local('task_type') }}</p>
                    <fv-combobox
                        :model-value="selectOption(bench.task_type, taskTypeOptions)"
                        :options="taskTypeOptions"
                        :disabled="!lock"
                        @update:modelValue="updateOptionField(bench, 'task_type', $event)"
                    ></fv-combobox>
                </div>

                <div class="bench-field">
                    <p class="bench-field-label">{{ local('eval_type') }}</p>
                    <fv-combobox
                        :model-value="selectOption(bench.eval_type, evalTypeOptions)"
                        :options="evalTypeOptions"
                        :disabled="!lock"
                        @update:modelValue="updateOptionField(bench, 'eval_type', $event)"
                    ></fv-combobox>
                </div>

                <div class="bench-field full">
                    <p class="bench-field-label">{{ local('problem_path') }}</p>
                    <div class="bench-path-row">
                        <fv-button
                            theme="light"
                            background="#facf5c"
                            border-radius="6"
                            style="width: 30px; height: 30px; flex-shrink: 0"
                            :disabled="!lock"
                            :title="local('Select from Resource')"
                            @click="openResourcePanel(bench, 'problem_path')"
                        >
                            <i class="ms-Icon ms-Icon--GiftboxOpen"></i>
                        </fv-button>
                        <fv-breadcrumb
                            v-model="bench.problem_path"
                            class="bench-breadcrumb"
                            :border-radius="6"
                            :font-size="'12px'"
                            :disabled="true"
                            :title="bench.problem_path"
                            @click="toggleDialog(buildBenchPath(index), 'problem_path', true)"
                        ></fv-breadcrumb>
                        <fv-button
                            theme="light"
                            background="#facf5c"
                            border-radius="6"
                            style="width: 30px; height: 30px; flex-shrink: 0"
                            :disabled="!lock"
                            @click="toggleDialog(buildBenchPath(index), 'problem_path', true)"
                        >
                            <i class="ms-Icon ms-Icon--FolderOpen"></i>
                        </fv-button>
                    </div>
                    <directory-selector
                        v-model="dialogState[dialogKey(buildBenchPath(index), 'problem_path')]"
                        v-model:filePath="bench.problem_path"
                    ></directory-selector>
                </div>

                <div v-if="bench.task_type === 'text2sql'" class="bench-field full">
                    <p class="bench-field-label">{{ local('text2sql_dir') }}</p>
                    <div class="bench-path-row">
                        <fv-button
                            theme="light"
                            background="#facf5c"
                            border-radius="6"
                            style="width: 30px; height: 30px; flex-shrink: 0"
                            :disabled="!lock"
                            :title="local('Select from Resource')"
                            @click="openResourcePanel(bench, 'text2sql_dir')"
                        >
                            <i class="ms-Icon ms-Icon--GiftboxOpen"></i>
                        </fv-button>
                        <fv-breadcrumb
                            v-model="bench.text2sql_dir"
                            class="bench-breadcrumb"
                            :border-radius="6"
                            :font-size="'12px'"
                            :disabled="true"
                            :title="bench.text2sql_dir"
                            @click="toggleDialog(buildBenchPath(index), 'text2sql_dir', true)"
                        ></fv-breadcrumb>
                        <fv-button
                            theme="light"
                            background="#facf5c"
                            border-radius="6"
                            style="width: 30px; height: 30px; flex-shrink: 0"
                            :disabled="!lock"
                            @click="toggleDialog(buildBenchPath(index), 'text2sql_dir', true)"
                        >
                            <i class="ms-Icon ms-Icon--FolderOpen"></i>
                        </fv-button>
                    </div>
                    <directory-selector
                        v-model="dialogState[dialogKey(buildBenchPath(index), 'text2sql_dir')]"
                        v-model:filePath="bench.text2sql_dir"
                    ></directory-selector>
                </div>

                <div class="bench-field">
                    <p class="bench-field-label">{{ local('case_num') }}</p>
                    <fv-text-box
                        :model-value="formatNumber(bench.case_num)"
                        :placeholder="local('case_num')"
                        border-radius="6"
                        :border-width="2"
                        :reveal-border="true"
                        :disabled="!lock"
                        :border-color="'rgba(120, 120, 120, 0.1)'"
                        :focus-border-color="color"
                        :is-box-shadow="true"
                        underline
                        @update:modelValue="updateNumberField(bench, 'case_num', $event)"
                    ></fv-text-box>
                </div>

                <div class="bench-field">
                    <p class="bench-field-label">{{ local('batch_size') }}</p>
                    <fv-text-box
                        :model-value="formatNumber(bench.batch_size)"
                        :placeholder="local('batch_size')"
                        border-radius="6"
                        :border-width="2"
                        :reveal-border="true"
                        :disabled="!lock"
                        :border-color="'rgba(120, 120, 120, 0.1)'"
                        :focus-border-color="color"
                        :is-box-shadow="true"
                        underline
                        @update:modelValue="updateNumberField(bench, 'batch_size', $event)"
                    ></fv-text-box>
                </div>

                <div class="bench-field full">
                    <p class="bench-field-label">{{ local('key_mapping') }}</p>
                    <editor-preview
                        :model-value="keyMappingValue(buildBenchPath(index), bench.key_mapping)"
                        language="json"
                        :lock="lock"
                        @update:modelValue="
                            updateJsonField(buildBenchPath(index), bench, 'key_mapping', $event)
                        "
                    ></editor-preview>
                </div>
            </div>

            <div class="secondary-section">
                <div class="secondary-section-header">
                    <p class="bench-field-label">{{ local('secondary_benches') }}</p>
                </div>
                <bench-list-input
                    :list-value="bench.secondary_benches"
                    :lock="lock"
                    :nested-allowed-values="nestedAllowedValues"
                    :path-prefix="`${buildBenchPath(index)}.secondary_benches`"
                    :nested="true"
                ></bench-list-input>
            </div>
        </div>
        <resource-panel
            v-model="show.dataset"
            :title="local('Dataset')"
            mode="read"
            @confirm="handleDatasetConfirm"
        ></resource-panel>
    </div>
</template>

<script>
import { mapState } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'
import { useTheme } from '@/stores/theme'

import directorySelector from '@/components/general/directorySelector.vue'
import editorPreview from '@/components/manage/config/valueInput/editorPreview.vue'
import resourcePanel from '@/components/manage/mainFlow/panels/resourcePanel/index.vue'

export default {
    name: 'BenchListInput',
    components: {
        directorySelector,
        editorPreview,
        resourcePanel
    },
    props: {
        modelValue: {
            default: null
        },
        listValue: {
            default: null
        },
        lock: {
            default: true
        },
        nestedAllowedValues: {
            default: null
        },
        pathPrefix: {
            default: 'bench_list'
        },
        nested: {
            default: false
        }
    },
    data() {
        return {
            dialogState: {},
            jsonDrafts: {},
            benchUid: 0,
            benchUidMap: new WeakMap(),
            currentSelectTarget: null,
            show: {
                dataset: false
            }
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        ...mapState(useTheme, ['color']),
        benchItems: {
            get() {
                this.ensureListReady()
                return this.listValue !== null ? this.listValue : this.modelValue.value
            },
            set(value) {
                if (this.listValue !== null) return value
                this.modelValue.value = value
                return value
            }
        },
        nestedOptions() {
            return (
                this.nestedAllowedValues ||
                this.modelValue?.nested_allowed_values ||
                this.modelValue?.json_schema_extra?.nested_allowed_values ||
                {}
            )
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
    watch: {
        modelValue: {
            handler() {
                this.ensureListReady()
            },
            immediate: true,
            deep: true
        },
        listValue: {
            handler() {
                this.ensureListReady()
            },
            immediate: true,
            deep: true
        }
    },
    methods: {
        ensureListReady() {
            const current = this.listValue !== null ? this.listValue : this.modelValue?.value
            if (Array.isArray(current)) {
                current.forEach((bench) => this.normalizeBench(bench))
                return
            }
            if (this.listValue !== null) return
            if (!this.modelValue) return
            if (this.modelValue.value === null || this.modelValue.value === undefined) {
                this.modelValue.value = []
                return
            }
            if (!Array.isArray(this.modelValue.value)) this.modelValue.value = []
        },
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
        addBench() {
            this.benchItems.push({
                name: '',
                task_type: this.taskTypeOptions[0]?.key || '',
                problem_path: '',
                eval_type: this.evalTypeOptions[0]?.key || '',
                key_mapping: {},
                text2sql_dir: '',
                case_num: null,
                batch_size: null,
                secondary_benches: []
            })
        },
        removeBench(index) {
            this.benchItems.splice(index, 1)
        },
        buildBenchPath(index) {
            return `${this.pathPrefix}[${index}]`
        },
        dialogKey(path, field) {
            return `${path}.${field}`
        },
        toggleDialog(path, field, value) {
            this.dialogState[this.dialogKey(path, field)] = value
        },
        openResourcePanel(bench, field) {
            this.currentSelectTarget = { bench, field }
            this.show.dataset = true
        },
        handleDatasetConfirm(event) {
            if (!this.currentSelectTarget?.bench || !this.currentSelectTarget?.field) return
            this.currentSelectTarget.bench[this.currentSelectTarget.field] = event.path
            this.show.dataset = false
            this.currentSelectTarget = null
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
        updateOptionField(bench, field, value) {
            bench[field] = value?.key || ''
            if (field === 'task_type' && bench[field] !== 'text2sql') {
                bench.text2sql_dir = ''
            }
        },
        benchKey(bench, index) {
            if (!bench || typeof bench !== 'object') return `${this.pathPrefix}-${index}`
            if (!this.benchUidMap.has(bench)) {
                this.benchUid += 1
                this.benchUidMap.set(bench, `${this.pathPrefix}-${this.benchUid}`)
            }
            return this.benchUidMap.get(bench)
        },
        formatNumber(value) {
            if (value === null || value === undefined) return ''
            return String(value)
        },
        updateNumberField(bench, field, value) {
            if (value === '' || value === null || value === undefined) {
                bench[field] = null
                return
            }
            const parsed = parseInt(value, 10)
            bench[field] = Number.isNaN(parsed) ? null : parsed
        },
        keyMappingValue(path, value) {
            if (this.jsonDrafts[path] !== undefined) return this.jsonDrafts[path]
            if (value === null || value === undefined || value === '') return '{}'
            if (typeof value === 'string') return value
            try {
                return JSON.stringify(value, null, 2)
            } catch (error) {
                return '{}'
            }
        },
        updateJsonField(path, bench, field, value) {
            this.jsonDrafts[path] = value
            if (value === '') {
                bench[field] = {}
                return
            }
            try {
                bench[field] = JSON.parse(value)
            } catch (error) {
                // Keep the draft while the user is editing invalid JSON.
            }
        }
    }
}
</script>

<style lang="scss">
.bench-list-input {
    width: 100%;
    display: flex;
    flex-direction: column;
    gap: 12px;

    .bench-list-toolbar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
    }

    .bench-list-summary {
        font-size: 12px;
        color: var(--lp-text);

        span {
            font-weight: 700;
            color: var(--lp-text);
        }
    }

    .bench-list-empty {
        padding: 16px;
        border-radius: 10px;
        background: var(--lp-surface);
        border: 1px dashed var(--lp-line);
        font-size: 12px;
        color: var(--lp-text-mute);
    }

    .bench-card {
        padding: 14px;
        border-radius: 14px;
        background: var(--lp-surface);
        border: var(--lp-line) solid thin;
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
        color: var(--lp-text);
    }

    .bench-card-subtitle {
        margin-top: 2px;
        font-size: 11px;
        color: var(--lp-text-mute);
    }

    .bench-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
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
        color: var(--lp-text);
    }

    .bench-path-row {
        display: flex;
        align-items: center;
        gap: 6px;
    }

    .bench-breadcrumb {
        flex: 1;
        flex-shrink: 0;
        background: var(--lp-raised);

        &:hover {
            background: var(--lp-raised);
        }
    }

    .secondary-section {
        margin-top: 16px;
        padding-top: 12px;
        border-top: var(--lp-line) solid thin;
    }

    .secondary-section-header {
        margin-bottom: 10px;
    }
}

@media (max-width: 900px) {
    .bench-list-input {
        .bench-grid {
            grid-template-columns: 1fr;
        }
    }
}
</style>
