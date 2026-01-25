<template>
    <basePanel v-model="thisValue" :title="title" width="min(1200px, 90%)" height="80%" theme="light">
        <template v-slot:content>
            <div class="panel-dataset-content-block">
                <fv-Collapse :theme="theme" v-model="show.add" class="db-add-item" icon="Marquee"
                    :title="local('Add Database')" :content="local('Add new database information.')"
                    :disabled-collapse="true" :max-height="'auto'">
                    <template v-slot:icon>
                        <fv-img :src="img.database" style="width: auto; height: 30px; margin: 0px 5px"></fv-img>
                    </template>
                    <template v-slot:extension>
                        <fv-button v-show="show.add" theme="dark" :is-box-shadow="true" :background="gradient"
                            :disabled="!lock.add || !checkAdd()" border-radius="6"
                            style="width: 90px; margin-right: 5px" @click="confirmAdd">
                            {{ local('Confirm') }}
                        </fv-button>
                        <fv-button :theme="show.add ? theme : 'dark'" :is-box-shadow="true"
                            :background="show.add ? '' : gradient" border-radius="6" style="width: 90px"
                            @click="handleAdd">
                            {{ show.add ? local('Cancel') : local('Add') }}
                        </fv-button>
                    </template>
                    <template v-slot:default>
                        <div class="db-add-item-row column">
                            <p class="db-add-item-light-title">{{ local('Database Name') }}</p>
                            <fv-text-box :theme="theme" v-model="databaseName" :placeholder="local('Database Name')"
                                border-radius="6" :reveal-border="true" :is-box-shadow="true"></fv-text-box>
                        </div>
                        <hr />
                        <div class="db-add-item-row column" style="gap: 5px;">
                            <p class="db-add-item-light-title">{{ local('File Path') }}</p>
                            <fv-breadcrumb v-model="filePath" :border-radius="6" :font-size="'16px'" :disabled="true"
                                :title="thisValue" style="flex: 1; flex-shrink: 0" @click="show.dir = true">
                            </fv-breadcrumb>
                            <directory-selector v-model="show.dir" v-model:filePath="filePath"></directory-selector>
                            <fv-button theme="dark" icon="Folder" :is-box-shadow="true" :background="gradient"
                                border-radius="6" style="width: 120px;" @click="show.dir = true">
                                {{ local('Select Path') }}
                            </fv-button>
                        </div>
                        <hr />
                        <div class="db-add-item-row column">
                            <p class="db-add-item-light-title">{{ local('Description') }}</p>
                            <fv-text-box :theme="theme" v-model="databaseDescription"
                                :placeholder="local('Description')" border-radius="6" :reveal-border="true"
                                :is-box-shadow="true"></fv-text-box>
                        </div>
                    </template>
                </fv-Collapse>
                <fv-Collapse v-model="item.expanded" v-for="(item, index) in datasets" :key="index" class="dataset-item"
                    :title="item.name" :content="numSamples(item)" :maxHeight="item.showPreview ? 690 : 520"
                    background="rgba(251, 251, 251, 1)">
                    <template v-slot:icon>
                        <fv-img :src="img.database" style="width: auto; height: 30px; margin: 0px 5px"></fv-img>
                    </template>
                    <data-info v-if="!item.showPreview" :item="item"></data-info>
                    <table-info v-if="item.showPreview" :item="item" @back="item.showPreview = false"></table-info>
                    <template v-slot:extension>
                        <fv-button theme="dark" :icon="item.showPreview ? 'Hide' : 'View'"
                            :background="'linear-gradient(130deg, rgba(229, 123, 67, 1), rgba(225, 107, 56, 1))'"
                            :borderRadius="8" :isBoxShadow="true" style="margin-right: 5px"
                            @click="showPreview(item, $event)">{{ item.showPreview ? local('Hide') : local('Preview') }}
                        </fv-button>
                        <fv-button v-show="mode === 'read'" theme="dark" icon="Touch" :background="gradient"
                            :borderRadius="8" :isBoxShadow="true" @click="selectDataset($event, item)">{{
                            local('Select') }}
                        </fv-button>
                        <fv-button v-show="mode === 'edit'" theme="dark" icon="RemoveFrom" :background="'rgba(200, 38, 45, 1)'" :borderRadius="8"
                            :isBoxShadow="true" @click="removeDataset($event, item)">{{ local('Remove') }}
                        </fv-button>
                    </template>
                </fv-Collapse>
            </div>
        </template>
        <template v-slot:control="{ close }">
            <fv-button :borderRadius="8" :isBoxShadow="true" style="width: 120px; margin-right: 8px" @click="close">{{
                local('Close') }}</fv-button>
        </template>
    </basePanel>
</template>

<script>
import { mapState, mapActions } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'
import { useLoopAI } from '@/stores/loopAI'
import { useTheme } from '@/stores/theme'

import basePanel from '@/components/general/basePanel.vue'
import dataInfo from './preview/dataInfo.vue'
import tableInfo from './preview/tableInfo.vue'
import directorySelector from '@/components/general/directorySelector.vue'

import databaseIcon from '@/assets/flow/database.svg'

export default {
    components: {
        basePanel,
        dataInfo,
        tableInfo,
        directorySelector,
    },
    props: {
        modelValue: {
            default: false
        },
        title: {
            default: 'Dataset'
        },
        mode: {
            default: 'edit'
        }
    },
    data() {
        return {
            thisValue: this.modelValue,
            databaseName: '',
            databaseDescription: '',
            filePath: '',
            img: {
                database: databaseIcon
            },
            show: {
                add: false,
                dir: false
            },
            lock: {
                add: true
            }
        }
    },
    watch: {
        modelValue(val) {
            this.thisValue = val
            if (val) {
                this.getDatasets()
            }
        },
        thisValue(val) {
            this.$emit('update:modelValue', val)
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        ...mapState(useLoopAI, ['datasets']),
        ...mapState(useTheme, ['theme', 'color', 'gradient']),
        numSamples() {
            return (item) => {
                return `${this.local('Size')}: ${(item.size / 1000).toFixed(2)} KB`
            }
        }
    },
    mounted() { },
    methods: {
        ...mapActions(useLoopAI, ['getDatasets']),
        selectDataset(event, item) {
            event.stopPropagation()
            this.$emit('confirm', item)
        },
        checkAdd() {
            if (this.databaseName === '' || this.databaseDescription === '' || this.filePath === '') {
                return false
            }
            return true
        },
        handleAdd() {
            this.show.add ^= true
            this.databaseName = ''
            this.databaseDescription = ''
            this.filePath = ''
        },
        confirmAdd() {
            if (!this.checkAdd()) {
                return
            }
            if (!this.lock.add) {
                return
            }
            this.lock.add = false
            this.$api.dataset.createDataset(this.databaseName, this.databaseDescription, this.filePath).then((res) => {
                if (res.code === 200) {
                    this.$barWarning(this.local('Add Database Success'), {
                        status: 'correct'
                    })
                    this.handleAdd()
                    this.getDatasets()
                } else {
                    this.$barWarning(this.local('Add Database Failed') + ': ' + res.message, {
                        status: 'warning'
                    })
                }
                this.lock.add = true
            })
        },
        removeDataset(event, item) {
            event.stopPropagation()
            this.$infoBox(this.local('Confirm Delete Database') + ': ' + item.name, {
                status: 'error',
                theme: this.theme,
                confirm: () => {
                    this.$api.dataset.deleteDataset(item.id).then((res) => {
                        if (res.code === 200) {
                            this.$barWarning(this.local('Delete Database Success'), {
                                status: 'correct'
                            })
                            this.getDatasets()
                        } else {
                            this.$barWarning(this.local('Delete Database Failed') + ': ' + res.message, {
                                status: 'warning'
                            })
                        }
                    })
                }
            })
        },
        showPreview(item, event) {
            event.stopPropagation()
            item.expanded = true
            item.showPreview = !item.showPreview
        }
    }
}
</script>

<style lang="scss">
.panel-dataset-content-block {
    position: relative;
    width: 100%;
    height: 100%;
    gap: 5px;
    display: flex;
    flex-direction: column;
    overflow: overlay;

    .dataset-item {
        flex-shrink: 0;

        .collapse-item-content {
            position: relative;
            height: auto;
            transition: all 0.3s;
        }
    }

    .db-add-item {
        flex-shrink: 0;

        .collapse-item-content {
            position: relative;
            height: auto;
            transition: all 0.3s;
        }

        .db-add-item-title {
            margin: 5px 0px;
            font-size: 13.8px;
            font-weight: bold;
            color: rgba(123, 139, 209, 1);
            user-select: none;
        }

        .db-add-item-light-title {
            margin: 5px 0px;
            font-size: 12px;
            color: rgba(95, 95, 95, 1);
            user-select: none;
        }

        .db-add-item-info {
            margin: 5px 0px;
            font-size: 12px;
            color: rgba(120, 120, 120, 1);
            user-select: none;
        }

        .db-add-item-std-info {
            font-size: 13.8px;
            color: rgba(27, 27, 27, 1);
            user-select: none;
        }

        .db-add-item-bold-info {
            margin: 5px 0px;
            font-size: 16px;
            font-weight: bold;
            color: rgba(27, 27, 27, 1);
            user-select: none;
        }

        .db-add-item-p-block {
            position: relative;
            width: 100%;
            height: auto;
            -padding: 15px 0px;
            box-sizing: border-box;
            line-height: 3;
            display: flex;
            flex-direction: column;
        }

        .db-add-item-row {
            position: relative;
            width: 100%;
            -padding: 0px 42px;
            flex-wrap: wrap;
            box-sizing: border-box;
            display: flex;
            align-items: center;

            &.no--pad {
                -padding: 0px;
            }

            &.sep {
                justify-content: space-between;
            }

            &.column {
                flex-direction: column;
                align-items: flex-start;
            }

            &.full {
                flex: 1;
            }

            &.auto {
                overflow: auto;
            }
        }

        hr {
            margin: 10px 0px;
            border: none;
            border-top: rgba(120, 120, 120, 0.1) solid thin;
        }
    }
}
</style>
