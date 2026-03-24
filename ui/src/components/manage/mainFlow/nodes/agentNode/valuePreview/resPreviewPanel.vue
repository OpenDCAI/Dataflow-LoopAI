<template>
    <basePanel
        v-model="thisValue"
        :title="title"
        width="min(1200px, 90%)"
        height="80%"
        theme="light"
        :teleport="true"
    >
        <template v-slot:content>
            <div class="panel-resource-preview-content-block">
                <component
                    :is="computedUI"
                    :is-show="thisValue"
                    :item="{ id: computedPath }"
                    :showBack="!readOnly"
                >
                </component>
            </div>
        </template>
        <template v-slot:control="{ close }">
            <fv-button
                :borderRadius="8"
                :isBoxShadow="true"
                style="width: 120px; margin-right: 8px"
                @click="close"
                >{{ local('Close') }}</fv-button
            >
        </template>
    </basePanel>
</template>

<script>
import { mapState, mapActions } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'
import { useTheme } from '@/stores/theme'

import basePanel from '@/components/general/basePanel.vue'
import tableInfo from '@/components/manage/mainFlow/panels/resourcePanel/preview/tableInfo.vue'
import textInfo from '@/components/manage/mainFlow/panels/resourcePanel/preview/textInfo.vue'

export default {
    components: {
        basePanel,
        tableInfo,
        textInfo
    },
    props: {
        modelValue: {
            default: false
        },
        filePath: {
            default: ''
        },
        title: {
            default: 'Resource Previewer'
        },
        readOnly: {
            default: false
        }
    },
    data() {
        return {
            thisValue: this.modelValue
        }
    },
    watch: {
        modelValue(val) {
            this.thisValue = val
        },
        thisValue(val) {
            this.$emit('update:modelValue', val)
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        ...mapState(useTheme, ['theme', 'color', 'gradient']),
        computedUI() {
            let path = this.filePath.split('/')
            let fileName = path[path.length - 1]
            let fileExt = fileName.split('.').pop()
            let supportTable = ['csv', 'tsv', 'json', 'jsonl']
            if (supportTable.includes(fileExt)) {
                return tableInfo
            }
            return textInfo
        },
        computedPath() {
            return `file:///${this.filePath}`
        }
    },
    mounted() {},
    methods: {}
}
</script>

<style lang="scss">
.panel-resource-preview-content-block {
    position: relative;
    width: 100%;
    height: 100%;
    gap: 5px;
    display: flex;
    flex-direction: column;
    overflow: overlay;
}
</style>
