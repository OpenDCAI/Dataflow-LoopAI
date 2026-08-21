<template>
    <div class="model-register-row">
        <div class="model-register-topbar">
            <span class="model-tier-badge" :class="model.tier">{{ model.tier }}</span>
            <p class="model-register-name">{{ model.name || local('Model Name') }}</p>
            <div class="model-register-controls">
                <fv-toggle-switch
                    v-model="model.enabled"
                    :on="local('Enabled')"
                    :off="local('Disabled')"
                    :width="76"
                    :height="25"
                    :inside-content="true"
                    :switch-on-background="color"
                ></fv-toggle-switch>
                <fv-button
                    theme="dark"
                    border-radius="6"
                    background="rgba(200, 38, 45, 1)"
                    :title="local('Remove')"
                    style="width: 32px; height: 32px"
                    @click="$emit('remove', index)"
                >
                    <i class="ms-Icon ms-Icon--Delete"></i>
                </fv-button>
            </div>
        </div>
        <div class="model-register-fields">
            <div class="field-item">
                <span>{{ local('Tier') }}</span>
                <fv-combobox
                    v-model="tierModel"
                    :placeholder="local('Select Tier')"
                    :options="tierOptions"
                    :choosen-slider-background="color"
                    border-radius="6"
                    style="width: 100%"
                ></fv-combobox>
            </div>
            <div class="field-item">
                <span>{{ local('Name') }}</span>
                <fv-text-box
                    v-model="model.name"
                    border-radius="3"
                    :border-width="2"
                    :reveal-border="true"
                    :border-color="'rgba(120, 120, 120, 0.1)'"
                    :focus-border-color="color"
                    :is-box-shadow="true"
                    underline
                    style="width: 100%"
                ></fv-text-box>
            </div>
            <div class="field-item">
                <span>{{ local('Model Name') }}</span>
                <fv-text-box
                    v-model="model.model_name"
                    border-radius="3"
                    :border-width="2"
                    :reveal-border="true"
                    :border-color="'rgba(120, 120, 120, 0.1)'"
                    :focus-border-color="color"
                    :is-box-shadow="true"
                    underline
                    style="width: 100%"
                ></fv-text-box>
            </div>
            <div class="field-item wide">
                <span>{{ local('Base URL') }}</span>
                <fv-text-box
                    v-model="model.base_url"
                    border-radius="3"
                    :border-width="2"
                    :reveal-border="true"
                    :border-color="'rgba(120, 120, 120, 0.1)'"
                    :focus-border-color="color"
                    :is-box-shadow="true"
                    underline
                    style="width: 100%"
                ></fv-text-box>
            </div>
            <div class="field-item wide">
                <span>{{ local('API Key') }}</span>
                <fv-text-box
                    v-model="model.api_key"
                    border-radius="3"
                    :border-width="2"
                    :reveal-border="true"
                    :border-color="'rgba(120, 120, 120, 0.1)'"
                    :focus-border-color="color"
                    :is-box-shadow="true"
                    underline
                    style="width: 100%"
                ></fv-text-box>
            </div>
            <div class="field-item">
                <span>{{ local('Max Worker') }}</span>
                <fv-text-box
                    v-model="model.maxworker"
                    border-radius="3"
                    :border-width="2"
                    :reveal-border="true"
                    :border-color="'rgba(120, 120, 120, 0.1)'"
                    :focus-border-color="color"
                    :is-box-shadow="true"
                    underline
                    style="width: 100%"
                ></fv-text-box>
            </div>
            <div class="field-item">
                <span>{{ local('Response API') }}</span>
                <fv-combobox
                    v-model="wireApiModel"
                    :placeholder="local('Select Response API')"
                    :options="wireApiOptions"
                    :choosen-slider-background="color"
                    border-radius="6"
                    style="width: 100%"
                ></fv-combobox>
            </div>
            <div class="field-item">
                <span>{{ local('Response Format') }}</span>
                <fv-combobox
                    v-model="responseFormatModel"
                    :placeholder="local('Select Response Format')"
                    :options="responseFormatOptions"
                    :choosen-slider-background="color"
                    border-radius="6"
                    style="width: 100%"
                ></fv-combobox>
            </div>
        </div>
    </div>
</template>

<script>
import { mapState } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'
import { useTheme } from '@/stores/theme'

export default {
    name: 'ModelPoolItem',
    props: {
        index: { type: Number, required: true },
        model: { type: Object, required: true },
        tierOptions: { type: Array, required: true },
        wireApiOptions: { type: Array, required: true },
        responseFormatOptions: { type: Array, required: true }
    },
    emits: ['remove'],
    computed: {
        ...mapState(useAppConfig, ['local']),
        ...mapState(useTheme, ['theme', 'color']),
        tierModel: {
            get() {
                let item = this.tierOptions.find((tier) => tier.key === this.model.tier)
                return item || {}
            },
            set(value) {
                this.model.tier = value.key || ''
            }
        },
        wireApiModel: {
            get() {
                let item = this.wireApiOptions.find((wire) => wire.key === this.model.wire_api)
                return item || {}
            },
            set(value) {
                this.model.wire_api = value.key || ''
            }
        },
        responseFormatModel: {
            get() {
                let item = this.responseFormatOptions.find(
                    (format) => format.key === this.model.response_format
                )
                return item || {}
            },
            set(value) {
                this.model.response_format = value.key || ''
            }
        }
    }
}
</script>

<style lang="scss" scoped>
.model-register-row {
    width: 100%;
    padding: 12px;
    box-sizing: border-box;
    border: 1px solid var(--lp-line);
    border-radius: 8px;
    background: var(--lp-surface);
    display: flex;
    flex-direction: column;
    gap: 12px;
}
.model-register-topbar {
    display: flex;
    align-items: center;
    gap: 8px;
}
.model-register-name {
    flex: 1;
    min-width: 0;
    font-size: 13px;
    font-weight: 600;
    color: var(--lp-text);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.model-register-controls {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-shrink: 0;
}
.model-tier-badge {
    flex-shrink: 0;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 11px;
    font-weight: 600;
    line-height: 16px;
    text-transform: uppercase;
    background: var(--lp-surface);
    color: rgba(96, 102, 120, 1);
}
.model-tier-badge.high {
    background: var(--lp-surface);
    color: var(--lp-text);
}
.model-tier-badge.medium {
    background: var(--lp-surface);
    color: rgba(44, 90, 168, 1);
}
.model-register-fields {
    display: grid;
    grid-template-columns: repeat(6, minmax(0, 1fr));
    gap: 10px 12px;
}
.model-register-fields .field-item {
    grid-column: span 2;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
}
.model-register-fields .field-item.wide {
    grid-column: span 3;
}
.model-register-fields span {
    font-size: 12px;
    color: var(--lp-text);
}
@media (max-width: 760px) {
    .model-register-fields {
        grid-template-columns: 1fr;
    }
    .model-register-fields .field-item,
    .model-register-fields .field-item.wide {
        grid-column: span 1;
    }
}
</style>
