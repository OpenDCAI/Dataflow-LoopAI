<template>
    <baseDrawer
        v-model="thisValue"
        :title="thisNodeProps.data.label"
        position="left"
        length="1200px"
        theme="light"
    >
        <template v-slot:content>
            <agentNode v-bind="thisNodeProps" class="detail-node-container" />
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
    </baseDrawer>
</template>

<script>
import { mapState, mapActions } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'
import { useTheme } from '@/stores/theme'

import baseDrawer from '@/components/general/baseDrawer.vue'
import agentNode from '@/components/manage/mainFlow/nodes/agentNode/index.vue'

export default {
    components: {
        baseDrawer,
        agentNode
    },
    props: {
        modelValue: {
            default: false
        },
        nodeProps: {
            default: () => ({})
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
        ...mapState(useTheme, ['color', 'gradient']),
        thisNodeProps() {
            let thisProps = Object.assign({}, this.nodeProps)
            thisProps.data.enableDetail = false
            thisProps.data.useSourceHandle = false
            thisProps.data.useTargetHandle = false
            return thisProps
        }
    },
    mounted() {},
    methods: {}
}
</script>

<style lang="scss">
.detail-node-container {
    height: 100%;

    .lp-flow-node-container {
        height: 100%;

        .remain-content-block {
            .col-wrapper {
                flex: 1;

                .train-state {
                    width: 400px;
                    flex: 1;
                }
            }
        }
    }
}
</style>
