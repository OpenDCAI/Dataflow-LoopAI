<template>
    <base-node v-bind="props" :data="thisData" :running="runningMe">
        <div class="fv-loading-block">
            <fv-progress-ring
                v-if="loading"
                :loading="true"
                :r="18"
                :border-width="3"
                background="white"
                :color="thisData.borderColor"
            ></fv-progress-ring>
        </div>
        <div class="node-row-item">
            <span
                class="info-title"
                style="font-size: 13px"
                :style="{ color: thisData.iconColor }"
                >{{ appConfig.local('State') }}</span
            >
        </div>
        <hr />
        <div class="node-group-item scroll-list" @wheel.stop>
            <div
                v-if="loopAIState"
                v-for="(item, index) in stateFiltered"
                :key="`run_${index}`"
                class="node-row-item col"
            >
                <span class="info-title">{{ item.key }}</span>
                <value-preview
                    v-model="item.value"
                    :modelKey="item.key"
                    :stateKey="thisData.stateKey"
                    :foreground="thisData.iconColor"
                    @mousedown.stop
                    @click.stop
                ></value-preview>
            </div>
        </div>
        <div v-if="customInfoFiltered.length > 0" class="node-row-item">
            <span
                class="info-title"
                style="font-size: 13px"
                :style="{ color: thisData.iconColor }"
                >{{ appConfig.local('Custom Info') }}</span
            >
        </div>
        <hr v-if="customInfoFiltered.length > 0" />
        <div v-if="customInfoFiltered.length > 0" class="node-group-item scroll-list" @wheel.stop>
            <div
                v-for="(custom_info, c_index) in customInfoFiltered"
                :key="`custom_${c_index}`"
                class="node-row-item col"
            >
                <span class="info-title" :style="{ color: thisData.iconColor }">{{
                    custom_info.key
                }}</span>
                <hr />
                <div class="node-row-item">
                    <span class="info-title">{{ appConfig.local('Message') }}</span>
                    <p class="info-value tiny" :title="custom_info.value.message">
                        {{ custom_info.value.message ? custom_info.value.message : 'null' }}
                    </p>
                </div>
                <div v-if="custom_info.value.progress" class="node-row-item col">
                    <span class="info-title">{{ appConfig.local('Progress') }}</span>
                    <fv-progress-bar
                        :model-value="custom_info.value.progress * 100"
                        :foreground="thisData.iconColor"
                        :background="'white'"
                        :border-radius="8"
                        style="width: 100%"
                    ></fv-progress-bar>
                </div>
                <span class="info-title" :style="{ color: thisData.iconColor }">{{
                    appConfig.local('Event Data')
                }}</span>
                <hr />
                <div
                    v-if="custom_info.value.data"
                    v-for="(item_val, item_key) in custom_info.value.data"
                    :key="`custom_item_${item_key}`"
                    class="node-row-item col"
                >
                    <span class="info-title">{{ item_key }}</span>
                    <fv-text-box
                        :model-value="item_val"
                        :placeholder="appConfig.local('Please input') + ` ${item_key}`"
                        font-size="12"
                        border-radius="8"
                        :reveal-border="true"
                        style="width: 100%; height: 35px"
                        @mousedown.stop
                        @click.stop
                    ></fv-text-box>
                </div>
            </div>
        </div>
    </base-node>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useGlobal } from '@/hooks/general/useGlobal'
import { useAppConfig } from '@/stores/appConfig'
import { useLoopAI } from '@/stores/loopAI'

import baseNode from '@/components/manage/mainFlow/nodes/baseNode.vue'
import valuePreview from './valuePreview.vue'

const { $api } = useGlobal()

const emits = defineEmits(['switch-database', 'update-node-data', 'update-run-value'])

const props = defineProps({
    id: {
        type: String,
        required: true
    },
    position: {
        type: Object,
        required: true
    },
    selected: {
        type: Boolean,
        default: false
    },
    data: {
        type: Object,
        default: () => ({})
    }
})

const appConfig = useAppConfig()
const loopAI = useLoopAI()

const defaultData = {
    label: 'Operator',
    status: 'Operator',
    nodeInfo: '',
    stateKey: 'trainer', // 用于匹配loopAI中的Sub-Agent的State
    graphClsPrefix: 'TrainerAgent', // 用于匹配custom_info的key
    include_nodes: [], // 用于侦测当前运行节点
    background: 'linear-gradient(130deg, rgba(161, 145, 206, 0.8), rgba(252, 252, 252, 0.8))',
    titleColor: '',
    statusColor: 'rgba(90, 90, 90, 1)',
    infoTitleColor: 'rgba(28, 28, 30, 1)',
    borderColor: '',
    shadowColor: '',
    groupBackground: 'rgba(255, 255, 255, 0.8)',
    enableDelete: true
}
const thisData = computed(() => {
    return {
        ...defaultData,
        ...props.data,
        shadowColor: props.data.nodeShadowColor ? props.data.nodeShadowColor : 'rgba(0, 0, 0, 0.05)'
    }
})

const loopAIState = computed(() => {
    return loopAI.taskStatus.state
})
const loopAIStateFiltered = computed(() => {
    let state = loopAI.taskStatus.state
    let filter_list = []
    if (!state) return filter_list
    if (!state[thisData.value.stateKey]) return filter_list
    for (let key in state[thisData.value.stateKey]) {
        let val = state[thisData.value.stateKey][key]
        if (val === null) val = null
        if (val === undefined) val = null
        filter_list.push({
            key,
            value: val
        })
    }
    return filter_list
})
const loopAIDefaultStateFiltered = computed(() => {
    let state = loopAI.taskStatus.state
    let filter_list = []
    if (!state) return filter_list
    if (!thisData.value.defaultStateKey) return filter_list
    for (let key of thisData.value.defaultStateKey) {
        let val = state[key]
        if (val === null) val = null
        if (val === undefined) val = null
        filter_list.push({
            key,
            value: val
        })
    }
    return filter_list
})
const stateFiltered = computed(() => {
    if (thisData.value.stateKey == 'default') return loopAIDefaultStateFiltered.value
    return loopAIStateFiltered.value
})

const customInfo = computed(() => {
    return loopAI.taskStatus.custom_info
})
const customInfoFiltered = computed(() => {
    let info = customInfo.value
    let filter_list = []
    if (!info) return filter_list
    let matchedPrefix = thisData.value.graphClsPrefix.split(',')
    for (let key in info) {
        let match = false
        for (let prefix of matchedPrefix)
            if (key.startsWith(prefix)) {
                match = true
                break
            }
        if (match) {
            filter_list.push({
                key,
                value: info[key]
            })
        }
    }
    return filter_list
})

const runningMe = computed(() => {
    try {
        return loopAI.taskStatus.running_tasks.some((task) => {
            return thisData.value.include_nodes.includes(task)
        })
    } catch (e) {
        return false
    }
})

const loading = ref(false)

onMounted(() => {})

const emitUpdateRunValue = (item) => {}
</script>

<style lang="scss">
.lp-flow-default-node {
    .fv-loading-block {
        @include HcenterVcenter;
    }

    .scroll-list {
        max-height: 350px;
        overflow-y: overlay;
    }
}
</style>
