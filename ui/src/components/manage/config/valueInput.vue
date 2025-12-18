<template>
    <div v-if="modelValue.type === 'str' || modelValue.type === 'int'">
        <fv-text-box
            v-model="modelValue.value"
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
    </div>
    <div v-else-if="modelValue.type === 'bool'">
        <fv-toggle-switch
            v-model="modelValue.value"
            :on="local('True')"
            :off="local('False')"
            :switch-on-background="color"
            :disabled="!lock"
        ></fv-toggle-switch>
    </div>
    <div class="row-item" v-else-if="modelValue.type === 'float'">
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
</template>

<script>
import { mapState } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'
import { useTheme } from '@/stores/theme'

export default {
    props: {
        modelValue: {
            default: () => ({})
        },
        name: {
            default: ''
        },
        lock: {
            default: true
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        ...mapState(useTheme, ['color']),
        slideValueModel: {
            get() {
                return this.modelValue.value * 100
            },
            set(value) {
                this.modelValue.value = value / 100
            }
        }
    }
}
</script>

<style lang="scss">
.row-item {
    width: 300px;
    gap: 5px;
    display: flex;
    align-items: center;
}
</style>
