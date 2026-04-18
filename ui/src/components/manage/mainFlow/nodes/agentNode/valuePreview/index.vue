<template>
    <div class="value-preview-row-item">
        <div class="not-overflow"></div>
        <p v-if="computedUIType === 'none'" class="none-value">None</p>
        <span v-if="computedUIType === 'default'" class="none-value">{{ thisValue }}</span>
        <editor-preview
            v-if="computedUIType === 'editor'"
            :model-value="thisValue"
            :language="editorLanguage"
        ></editor-preview>
        <fv-text-box
            v-if="computedUIType === 'text'"
            v-model="thisValue"
            :placeholder="local(modelKey)"
            border-radius="6"
            :border-width="2"
            :reveal-border="true"
            :disabled="!lock"
            :border-color="'rgba(120, 120, 120, 0.1)'"
            :focus-border-color="foreground"
            :is-box-shadow="true"
            icon="Set"
            underline
            style="width: 100%; height: 30px; margin-bottom: 3px"
            readonly
            @icon-click="copyText"
        ></fv-text-box>
        <fv-toggle-switch
            v-if="computedUIType === 'bool'"
            v-model="thisValue"
            :on="local('True')"
            :off="local('False')"
            :width="65"
            :height="25"
            :switch-on-background="foreground"
            :inside-content="true"
            :disabled="!lock"
        ></fv-toggle-switch>
        <fv-combobox
            v-if="computedUIType === 'list'"
            v-model="listValueModel"
            :placeholder="modelKey"
            :options="formatAllowedValues"
        ></fv-combobox>
        <div v-if="computedUIType === 'slider'" class="value-preview-row-item">
            <fv-slider
                v-model="slideValueModel"
                :showLabel="true"
                :unit="1"
                :color="foreground"
                :disabled="!lock"
                background="rgba(255, 255, 255, 0.8)"
                style="margin: 5px 0px; flex: 1"
            >
                <template v-slot="prop">
                    <span>{{ prop.modelValue / 100 }}</span>
                </template>
            </fv-slider>
            <fv-text-box
                v-model="thisValue"
                :placeholder="local(modelKey)"
                border-radius="3"
                :border-width="2"
                :reveal-border="true"
                :disabled="!lock"
                :border-color="'rgba(120, 120, 120, 0.1)'"
                :focus-border-color="foreground"
                :is-box-shadow="true"
                underline
                style="width: 80px"
                readonly
            ></fv-text-box>
        </div>
        <div v-if="computedUIType === 'dir'" class="value-preview-row-item">
            <fv-breadcrumb
                v-model="dirModel"
                :border-radius="6"
                :font-size="'10px'"
                :disabled="true"
                :title="thisValue"
                style="flex: 1; flex-shrink: 0"
                @click="show.dir = true"
            >
            </fv-breadcrumb>
            <component
                :is="dirComponent"
                v-model="show.dir"
                v-model:filePath="dirModel"
                :readOnly="true"
            ></component>
        </div>
        <fv-button
            v-show="false"
            theme="dark"
            background="rgba(111, 92, 196, 1)"
            border-radius="30"
            style="width: 25px; height: 25px; flex-shrink: 0"
            :title="local('Set as Default')"
            @click="setDefault"
        >
            <i class="ms-Icon ms-Icon--Leaf"></i>
        </fv-button>
        <fv-button
            v-show="false"
            theme="dark"
            background="rgba(200, 38, 45, 1)"
            border-radius="30"
            style="width: 25px; height: 25px; flex-shrink: 0"
            :title="local('Clear as None')"
            @click="clearNone"
        >
            <i class="ms-Icon ms-Icon--Delete"></i>
        </fv-button>
    </div>
</template>

<script>
import { mapState } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'
import { useLoopAI } from '@/stores/loopAI'
import { useTheme } from '@/stores/theme'

import resPreviewPanel from './resPreviewPanel.vue'
import editorPreview from './editorPreview.vue'
import directorySelector from '@/components/general/directorySelector.vue'

export default {
    components: { resPreviewPanel, directorySelector, editorPreview },
    props: {
        modelValue: { default: () => ({}) },
        modelKey: { default: '' },
        stateKey: { default: '' },
        foreground: { default: '' },
        lock: { default: true }
    },
    data() {
        return { thisValue: this.modelValue, show: { dir: false } }
    },
    watch: {
        modelValue() {
            this.thisValue = this.modelValue
        },
        thisValue() {
            this.$emit('update:modelValue', this.thisValue)
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        ...mapState(useTheme, ['color']),
        ...mapState(useLoopAI, ['stateSchema']),
        slideValueModel: {
            get() {
                return this.thisValue * 100
            },
            set(value) {
                this.thisValue = value / 100
            }
        },
        schemaModel() {
            if (!this.stateSchema) return null
            if (!this.stateSchema[this.stateKey]) return null
            return this.stateSchema[this.stateKey][this.modelKey] || null
        },
        computedUIType() {
            if (!this.schemaModel) return 'default'
            const listChoices = this.schemaModel.allowed_values || this.schemaModel.options
            if (this.schemaModel.ui_type === 'list' && listChoices && listChoices.length)
                return 'list'
            if (this.thisValue === null || this.thisValue === undefined) return 'none'
            if (this.schemaModel.ui_type === 'file_path') return 'dir'
            if (this.schemaModel.ui_type === 'textarea') return 'editor'
            if (this.schemaModel.ui_type === 'text' || this.schemaModel.ui_type === 'password')
                return 'text'
            if (
                this.schemaModel.ui_type === 'toggle_switch' ||
                this.schemaModel.ui_type === 'switch'
            )
                return 'bool'
            if (this.schemaModel.ui_type === 'slider') return 'slider'
            return 'text'
        },
        editorLanguage() {
            if (!this.schemaModel || !this.schemaModel.language) return 'plaintext'
            return this.schemaModel.language
        },
        listValueModel: {
            get() {
                let val = this.thisValue
                let item = this.formatAllowedValues.find((item) => item.key === val)
                return item ? item : {}
            },
            set(choosen) {
                this.thisValue = choosen.key
            }
        },
        formatAllowedValues() {
            if (!this.schemaModel) return []
            const raw = this.schemaModel.allowed_values || this.schemaModel.options
            if (!raw || !raw.length) return []
            return raw.map((item) => ({ key: item, text: item }))
        },
        dirModel: {
            get() {
                if (!this.thisValue) return ''
                return this.thisValue
            },
            set(value) {
                this.thisValue = value
            }
        },
        dirComponent() {
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
            let path = this.thisValue.split('/')
            let fileName = path[path.length - 1]
            let fileExt = fileName.split('.').pop().toLowerCase()
            if (allowedExts.includes(fileExt)) {
                return resPreviewPanel
            } else {
                return directorySelector
            }
        }
    },
    methods: {
        setDefault() {
            this.thisValue = this.schemaModel.default ? this.schemaModel.default : ''
        },
        clearNone() {
            this.thisValue = null
        },
        copyText() {
            navigator.clipboard.writeText(this.thisValue).then(() => {
                this.$barWarning(this.local('Copied'), { status: 'correct' })
            })
        }
    }
}
</script>

<style lang="scss">
.value-preview-row-item {
    width: 100%;
    gap: 5px;
    display: flex;
    align-items: center;
    overflow: visible;

    .none-value {
        @include HcenterVcenter;

        width: 100%;
        height: auto;
        flex: 1;
        padding: 5px 5px;
        background: rgba(255, 255, 255, 0.8);
        font-size: 12px;
        color: rgba(120, 120, 120, 1);
        border-radius: 6px;
        overflow-wrap: anywhere;
        user-select: none;
        overflow: hidden;
    }
}
</style>
