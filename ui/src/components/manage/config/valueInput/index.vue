<template>
    <div class="value-input-row-item">
        <p v-if="computedUIType === 'none'" class="none-value">None</p>
        <editor-preview
            v-if="computedUIType === 'editor'"
            :model-value="modelValue.value"
            :language="editorLanguage"
            :lock="lock"
            @update:modelValue="modelValue.value = $event"
        ></editor-preview>
        <fv-text-box
            v-if="computedUIType === 'text'"
            v-model="textModel"
            :left-icon="modelValue.ui_type === 'number' ? 'Keyboard12Key' : 'Characters'"
            :placeholder="local(name)"
            border-radius="3"
            :border-width="2"
            :reveal-border="true"
            :disabled="!lock"
            :border-color="'rgba(120, 120, 120, 0.1)'"
            :focus-border-color="color"
            :is-box-shadow="true"
            underline
        ></fv-text-box>
        <fv-toggle-switch
            v-if="computedUIType === 'bool'"
            v-model="modelValue.value"
            :on="local('True')"
            :off="local('False')"
            :width="65"
            :height="25"
            :switch-on-background="color"
            :inside-content="true"
            :disabled="!lock"
        ></fv-toggle-switch>
        <fv-combobox
            v-if="computedUIType === 'list'"
            v-model="listValueModel"
            :placeholder="modelValue.title"
            :options="formatAllowedValues"
        ></fv-combobox>
        <div v-if="computedUIType === 'slider'" class="value-input-row-item" style="width: 260px">
            <fv-slider
                v-model="slideValueModel"
                :showLabel="true"
                :unit="1"
                :color="color"
                :disabled="!lock"
                background="rgba(255, 255, 255, 0.8)"
                style="margin: 5px 0px; flex: 1"
            >
                <template v-slot="prop">
                    <span>{{ prop.modelValue / 100 }}</span>
                </template>
            </fv-slider>
            <fv-text-box
                v-model="modelValue.value"
                :placeholder="local(modelValue.name)"
                border-radius="3"
                :border-width="2"
                :reveal-border="true"
                :disabled="!lock"
                :border-color="'rgba(120, 120, 120, 0.1)'"
                :focus-border-color="color"
                :is-box-shadow="true"
                underline
                style="width: 80px"
            ></fv-text-box>
        </div>
        <div v-if="computedUIType === 'dir'" class="value-input-row-item" style="width: 260px">
            <fv-button
                theme="light"
                background="#facf5c"
                border-radius="6"
                style="width: 25px; height: 25px; flex-shrink: 0"
                :title="local('Select from Resource')"
                @click="$emit('select-dataset')"
            >
                <i class="ms-Icon ms-Icon--GiftboxOpen"></i>
            </fv-button>
            <fv-breadcrumb
                v-model="dirModel"
                class="breadcrumb-custom"
                :border-radius="6"
                :font-size="'12px'"
                :disabled="true"
                :title="modelValue.value"
                @click="show.dir = true"
            >
            </fv-breadcrumb>
            <directory-selector
                v-model="show.dir"
                v-model:filePath="dirModel"
                @cancel="modelValue.value = modelValue.default_value"
            ></directory-selector>
        </div>
        <fv-button
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
import { useTheme } from '@/stores/theme'

import directorySelector from '@/components/general/directorySelector.vue'
import editorPreview from '@/components/manage/config/valueInput/editorPreview.vue'

export default {
    components: { directorySelector, editorPreview },
    props: { modelValue: { default: () => ({}) }, name: { default: '' }, lock: { default: true } },
    data() {
        return { show: { dir: false } }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        ...mapState(useTheme, ['color']),
        textModel: {
            get() {
                return this.modelValue.value
                    ? this.modelValue.value.toString()
                    : this.modelValue.value
            },
            set(value) {
                if (value === null) {
                    this.modelValue.value = null
                    return
                }
                if (this.modelValue.type === 'int' || this.modelValue.ui_type === 'number')
                    value = parseInt(value)
                else if (this.modelValue.type === 'float' || this.modelValue.ui_type === 'slider')
                    value = parseFloat(value)
                this.modelValue.value = value
            }
        },
        slideValueModel: {
            get() {
                return this.modelValue.value * 100
            },
            set(value) {
                this.modelValue.value = value / 100
            }
        },
        computedUIType() {
            const listChoices = this.modelValue.allowed_values || this.modelValue.options
            if (this.modelValue.ui_type === 'list' && listChoices && listChoices.length)
                return 'list'
            if (this.modelValue.value === null || this.modelValue.value === undefined) return 'none'
            if (this.modelValue.ui_type === 'file_path') return 'dir'
            if (this.modelValue.ui_type === 'textarea') return 'editor'
            if (
                this.modelValue.type === 'str' ||
                this.modelValue.type === 'int' ||
                this.modelValue.ui_type === 'text' ||
                this.modelValue.ui_type === 'textarea' ||
                this.modelValue.ui_type === 'password'
            )
                return 'text'
            if (
                this.modelValue.type === 'bool' ||
                this.modelValue.ui_type === 'toggle_switch' ||
                this.modelValue.ui_type === 'switch'
            )
                return 'bool'
            if (this.modelValue.ui_type === 'slider') return 'slider'
            return 'text'
        },
        editorLanguage() {
            if (!this.modelValue || !this.modelValue.language) return 'plaintext'
            return this.modelValue.language
        },
        listValueModel: {
            get() {
                let val = this.modelValue.value
                let item = this.formatAllowedValues.find((item) => item.key === val)
                return item ? item : {}
            },
            set(choosen) {
                this.modelValue.value = choosen.key
            }
        },
        formatAllowedValues() {
            const raw = this.modelValue.allowed_values || this.modelValue.options
            if (!raw || !raw.length) return []
            return raw.map((item) => ({ key: item, text: item }))
        },
        dirModel: {
            get() {
                if (!this.modelValue.value) return ''
                return this.modelValue.value
            },
            set(value) {
                this.modelValue.value = value
            }
        }
    },
    methods: {
        setDefault() {
            this.modelValue.value = this.modelValue.default ? this.modelValue.default : ''
        },
        clearNone() {
            this.modelValue.value = null
        }
    }
}
</script>

<style lang="scss">
.value-input-row-item {
    width: 320px;
    gap: 5px;
    display: flex;
    align-items: center;
    overflow: visible;

    .none-value {
        @include HcenterVcenter;

        width: auto;
        height: 100%;
        flex: 1;
        padding: 5px 15px;
        background: rgba(255, 255, 255, 0.8);
        font-size: 12px;
        color: rgba(120, 120, 120, 1);
        border-radius: 6px;
        user-select: none;
    }

    .breadcrumb-custom {
        flex: 1;
        flex-shrink: 0;
        background: rgba(120, 120, 120, 0.1);

        &:hover {
            background: rgba(120, 120, 120, 0.2);
        }

        &:active {
            background: rgba(120, 120, 120, 0.3);
        }
    }
}
</style>
