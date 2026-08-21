<template>
    <basePanel v-model="thisValue" :title="panelTitle" width="1080px" height="85%" :theme="theme">
        <template v-slot:content>
            <div class="task-state-panel">
                <fv-pivot
                    v-if="pivotItems.length"
                    v-model="currentPivot"
                    class="state-pivot"
                    :items="pivotItems"
                    :tab="true"
                    :fontSize="12"
                    background="var(--lp-chrome)"
                    :sliderBackground="gradient"
                    :borderRadius="8"
                    padding="0px 5px"
                    itemPadding="0px 12px"
                    :sliderBorderRadius="12"
                ></fv-pivot>
                <div v-if="currentSectionKey" class="state-content-block">
                    <div
                        v-for="group in currentSectionGroups"
                        :key="group.key"
                        class="state-group-card"
                    >
                        <div class="state-group-header">
                            <p class="state-group-title">{{ group.name }}</p>
                            <p class="state-group-count">
                                {{ group.items.length }} {{ local('items') }}
                            </p>
                        </div>
                        <hr />
                        <div
                            v-for="(item, index) in group.items"
                            :key="`${group.key}-${item.key}`"
                            class="state-item-row"
                        >
                            <div class="state-item-meta">
                                <div class="state-item-title-row">
                                    <p class="state-item-title">
                                        {{ item.value.title || local(item.key) }}
                                    </p>
                                    <fv-callout
                                        v-if="item.value.description"
                                        effect="hover"
                                        position="bottomLeft"
                                    >
                                        <fv-button
                                            :font-size="10"
                                            borderRadius="30"
                                            style="width: 18px; height: 18px"
                                        >
                                            <i class="ms-Icon ms-Icon--Help"></i>
                                        </fv-button>
                                        <template v-slot:main>
                                            <p style="font-size: 13px">
                                                {{ item.value.description }}
                                            </p>
                                        </template>
                                    </fv-callout>
                                </div>
                                <p class="state-item-key">{{ item.key }}</p>
                            </div>
                            <value-input
                                :model-value="item.value"
                                :name="item.key"
                                :lock="lock.update"
                                @select-dataset="handleSelectDataset(item.value)"
                            ></value-input>
                            <hr v-if="index !== group.items.length - 1" />
                        </div>
                    </div>
                </div>
                <div v-else class="state-empty-block">
                    <p>{{ local('No state config available.') }}</p>
                </div>
            </div>
        </template>
        <template v-slot:control="{ close }">
            <fv-button
                theme="dark"
                icon="Go"
                :is-box-shadow="true"
                :background="gradient"
                :disabled="!lock.update"
                border-radius="6"
                style="width: 90px"
                @click="handleUpdate"
            >
                {{ local('Update') }}
            </fv-button>
            <fv-button
                icon="Refresh"
                :is-box-shadow="true"
                border-radius="6"
                :disabled="!lock.update"
                style="width: 90px"
                @click="handleReset"
            >
                {{ local('Reset') }}
            </fv-button>
            <fv-button :is-box-shadow="true" border-radius="6" style="width: 90px" @click="close">
                {{ local('Close') }}
            </fv-button>
        </template>
    </basePanel>
    <resource-panel
        v-model="show.dataset"
        :title="local('Dataset')"
        mode="read"
        @confirm="handleDatasetConfirm"
    ></resource-panel>
</template>

<script>
import { mapActions, mapState } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'
import { useTheme } from '@/stores/theme'
import { useLoopAI } from '@/stores/loopAI'

import basePanel from '@/components/general/basePanel.vue'
import valueInput from '@/components/manage/config/valueInput/index.vue'
import resourcePanel from '@/components/manage/mainFlow/panels/resourcePanel/index.vue'

export default {
    components: {
        basePanel,
        valueInput,
        resourcePanel
    },
    props: {
        modelValue: {
            default: false
        }
    },
    data() {
        return {
            thisValue: this.modelValue,
            currentPivot: null,
            currentSelectItem: null,
            taskStateConfigId: null,
            taskStateConfig: {
                id: null,
                task_id: null,
                name: '',
                states: {}
            },
            taskStateConfigInitial: {},
            lock: {
                update: true
            },
            show: {
                dataset: false
            }
        }
    },
    watch: {
        modelValue(val) {
            this.thisValue = val
            if (val) this.initPanel()
        },
        thisValue(val) {
            this.$emit('update:modelValue', val)
        },
        currentTask() {
            if (this.thisValue) this.initPanel()
        },
        pivotItems: {
            handler(items) {
                if (!items.length) {
                    this.currentPivot = null
                    return
                }
                const matchedItem = items.find((item) => item.key === this.currentPivot?.key)
                if (matchedItem) {
                    this.currentPivot = matchedItem
                    return
                }
                if (!this.currentPivot) {
                    this.currentPivot = items[0]
                }
            },
            immediate: true
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        ...mapState(useTheme, ['theme', 'gradient']),
        ...mapState(useLoopAI, ['currentTask']),
        panelTitle() {
            const taskName = this.currentTask?.name || this.local('Task')
            return `${taskName} ${this.local('States')}`
        },
        pivotItems() {
            const states = this.taskStateConfig?.states || {}
            return Object.keys(states).map((key) => ({
                key,
                name: () => key,
                width: 'auto'
            }))
        },
        currentSectionKey() {
            return this.currentPivot?.key || ''
        },
        currentSection() {
            if (!this.currentSectionKey) return {}
            return this.taskStateConfig?.states?.[this.currentSectionKey] || {}
        },
        currentSectionGroups() {
            const groups = {}
            Object.entries(this.currentSection).forEach(([key, value]) => {
                if (!value || typeof value !== 'object') return
                const groupKey = value.ui_group || 'default'
                if (!groups[groupKey]) {
                    groups[groupKey] = {
                        key: groupKey,
                        name: value.ui_group || this.local('Default'),
                        items: []
                    }
                }
                groups[groupKey].items.push({
                    key,
                    value
                })
            })
            return Object.values(groups)
        }
    },
    methods: {
        ...mapActions(useLoopAI, ['getTaskStateConfig', 'updateTaskStateConfig']),
        cloneJSON(value, fallback = {}) {
            try {
                return JSON.parse(JSON.stringify(value ?? fallback))
            } catch (error) {
                return JSON.parse(JSON.stringify(fallback))
            }
        },
        async initPanel() {
            if (!this.currentTask?.task_id) {
                this.taskStateConfigId = null
                this.taskStateConfig = {
                    id: null,
                    task_id: null,
                    name: '',
                    states: {}
                }
                this.taskStateConfigInitial = {}
                return
            }
            const res = await this.getTaskStateConfig(this.currentTask.task_id)
            if (res?.code === 200 && res.data) {
                const states = this.cloneJSON(res.data.states || res.data.config || {})
                this.taskStateConfigId = res.data.id
                this.taskStateConfig = {
                    id: res.data.id,
                    task_id: res.data.task_id || this.currentTask.task_id,
                    name: res.data.name || this.currentTask?.name || '',
                    states
                }
                this.taskStateConfigInitial = this.cloneJSON(states)
            }
        },
        handleSelectDataset(item) {
            this.show.dataset = true
            this.currentSelectItem = item
        },
        handleDatasetConfirm(event) {
            if (this.currentSelectItem === null) return
            this.currentSelectItem.value = event.path
            this.show.dataset = false
        },
        async handleUpdate() {
            if (!this.lock.update || !this.currentTask?.task_id) return
            this.lock.update = false
            try {
                const payload = {
                    id: this.taskStateConfigId,
                    task_id: this.currentTask.task_id,
                    name: this.taskStateConfig.name || this.currentTask?.name || '',
                    states: this.taskStateConfig.states
                }
                const res = await this.updateTaskStateConfig(payload, this.currentTask.task_id)
                if (res?.code === 200) {
                    this.taskStateConfigInitial = this.cloneJSON(this.taskStateConfig.states)
                    this.$barWarning(this.local('Update Config Success.'), {
                        status: 'correct'
                    })
                } else {
                    this.$barWarning(res?.message || this.local('Update Config Failed.'), {
                        status: 'warning'
                    })
                }
            } catch (error) {
                this.$barWarning(this.local('Update Config Failed.'), {
                    status: 'error'
                })
            }
            this.lock.update = true
        },
        handleReset() {
            const nextStates = this.cloneJSON(this.taskStateConfig.states)
            Object.keys(nextStates).forEach((sectionKey) => {
                const section = nextStates[sectionKey]
                if (!section || typeof section !== 'object') return
                Object.keys(section).forEach((fieldKey) => {
                    const field = section[fieldKey]
                    if (!field || typeof field !== 'object') return
                    field.value =
                        field.default_value === null ? '' : this.cloneJSON(field.default_value, '')
                })
            })
            this.taskStateConfig.states = nextStates
        }
    }
}
</script>

<style lang="scss">
.task-state-panel {
    position: relative;
    width: 100%;
    height: 100%;
    display: flex;
    flex-direction: column;
    overflow: hidden;

    .state-pivot {
        flex-shrink: 0;
    }

    .state-content-block {
        position: relative;
        width: 100%;
        flex: 1;
        padding-top: 15px;
        display: flex;
        flex-direction: column;
        gap: 12px;
        overflow: auto;
    }

    .state-group-card {
        position: relative;
        width: 100%;
        padding: 16px;
        background: var(--lp-surface);
        border: 1px solid var(--lp-line);
        border-radius: var(--lp-r-3);
    }

    .state-group-header {
        @include HbetweenVcenter;

        gap: 10px;
    }

    .state-group-title {
        font-size: var(--lp-t-md);
        font-weight: 600;
        color: rgba(35, 41, 70, 1);
    }

    .state-group-count {
        font-size: 12px;
        color: rgba(110, 118, 145, 1);
    }

    .state-item-row {
        position: relative;
        width: 100%;
        padding: 8px 0px;
        display: flex;
        flex-direction: column;
        gap: 10px;

        hr {
            margin: 8px 0px 0px;
            border: none;
            border-top: rgba(120, 120, 120, 0.1) solid thin;
        }
    }

    .state-item-meta {
        display: flex;
        flex-direction: column;
        gap: 5px;
    }

    .state-item-title-row {
        display: flex;
        align-items: center;
        gap: 6px;
    }

    .state-item-title {
        font-size: 13px;
        font-weight: 600;
        color: rgba(54, 61, 89, 1);
    }

    .state-item-key {
        font-size: 12px;
        color: rgba(125, 132, 156, 1);
    }

    .state-empty-block {
        @include HcenterVcenterC;

        flex: 1;
        font-size: 13px;
        color: rgba(120, 120, 120, 1);
    }
}
</style>
