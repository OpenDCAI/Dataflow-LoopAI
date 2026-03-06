<template>
    <transition name="task-slide">
        <div v-show="thisValue" class="lp-task-container">
            <div class="lp-task-header">
                <div class="left-block">
                    <fv-img class="logo" :src="img.task" alt="task"></fv-img>
                    <p class="title">{{ local('Tasks') }}</p>
                </div>
                <fv-button border-radius="8" style="width: 35px; height: 35px" @click="thisValue = false">
                    <i class="ms-Icon ms-Icon--ChevronLeft"></i>
                </fv-button>
            </div>
            <div class="lp-task-content">
                <hr />
                <div class="search-block">
                    <fv-text-box :placeholder="local('Search Tasks ...')" icon="Search" class="task-search-box"
                        :revealBorder="true" borderRadius="30" borderWidth="2" :isBoxShadow="true"
                        :focusBorderColor="color" :revealBorderColor="'rgba(103, 105, 251, 0.6)'"
                        :reveal-background-color="[
                            'rgba(103, 105, 251, 0.1)',
                            'rgba(103, 105, 251, 0.6)'
                        ]" @debounce-input="searchText = $event"></fv-text-box>
                    <div v-show="searchText" class="search-result-info">
                        {{ local('Total') }}: {{ totalNumVisible }} {{ local('tasks') }}
                        <p class="search-text">"{{ searchText }}"</p>
                    </div>
                </div>
                <hr />
                <fv-button icon="Add" border-radius="8" :is-box-shadow="true"
                    style="width: calc(100% - 20px); height: 40px; margin-left: 10px"
                    @click="(show.add = true), (addPanelMode = 'add')">{{ local('New Task') }}</fv-button>
                <div v-show="!lock.task" class="task-list-loading">
                    <fv-progress-ring loading="true" :r="20" :border-width="3" :color="color"
                        :background="'rgba(245, 245, 245, 1)'"></fv-progress-ring>
                </div>
                <div class="task-list-block">
                    <div v-show="item.show" v-for="(item, index) in tasks" :key="item.id" class="task-item"
                        :class="[{ choosen: thisTask && thisTask.task_id === item.task_id }]" @click="selectTask(item)"
                        @contextmenu="showRightMenu($event, item)">
                        <div class="task-item-main">
                            <div class="main-icon">
                                <i class="ms-Icon ms-Icon--DialShape3"></i>
                            </div>

                            <div class="content-block">
                                <p class="task-name" :title="item.name">{{ item.name }}</p>
                                <div class="row-item">
                                    <p class="task-info" :title="item.task_id">
                                        {{ local('Task ID') }}: {{ item.task_id }}
                                    </p>
                                    <time-rounder :model-value="new Date(item.updatedAt)" :foreground="color"
                                        style="width: auto"></time-rounder>
                                </div>
                            </div>
                        </div>
                        <hr />
                    </div>
                </div>
            </div>
            <task-panel v-model="show.add" :obj="currentContextItem" :addPanelMode="addPanelMode"
                @confirm="confirmTask"></task-panel>
            <fv-right-menu v-model="show.rightMenu" ref="rightMenu">
                <span @click="(show.add = true), (addPanelMode = 'add')">
                    <i class="ms-Icon ms-Icon--Add" :style="{ color: color }"></i>
                    <p>{{ local('New Task') }}</p>
                </span>
                <span @click="(show.add = true), (addPanelMode = 'rename')">
                    <i class="ms-Icon ms-Icon--Rename" :style="{ color: color }"></i>
                    <p>{{ local('Rename Task') }}</p>
                </span>
                <hr />
                <span @click="delTask(currentContextItem)">
                    <i class="ms-Icon ms-Icon--Delete" :style="{ color: '#c8323b' }"></i>
                    <p>{{ local('Delete Task') }}</p>
                </span>
            </fv-right-menu>
        </div>
    </transition>
</template>

<script>
import { mapState, mapActions } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'
import { useLoopAI } from '@/stores/loopAI'
import { useVueFlow } from '@vue-flow/core'
import { useTheme } from '@/stores/theme'

import timeRounder from '@/components/general/timeRounder.vue'
import taskPanel from '@/components/manage/mainFlow/panels/taskPanel.vue'

import taskIcon from '@/assets/flow/pipeline.svg'

export default {
    name: 'task',
    components: {
        timeRounder,
        taskPanel
    },
    props: {
        modelValue: {
            default: false
        },
        flowId: {
            default: ''
        },
        task: {
            default: null
        },
        loading: {
            default: false
        }
    },
    data() {
        return {
            thisValue: this.modelValue,
            thisLoading: this.loading,
            searchText: '',
            thisTask: null,
            currentContextItem: null,
            addPanelMode: 'add',
            show: {
                add: false,
                rightMenu: false
            },
            img: {
                task: taskIcon
            },
            lock: {
                task: true
            }
        }
    },
    watch: {
        modelValue(newValue) {
            this.thisValue = newValue
            if (newValue) {
                this.getConfigs()
                this.getTaskList()
            }
        },
        thisValue(newValue) {
            this.$emit('update:modelValue', newValue)
        },
        loading(newValue) {
            this.thisLoading = newValue
        },
        thisLoading(newValue) {
            this.$emit('update:loading', newValue)
        },
        task(newValue) {
            this.thisTask = newValue
        },
        thisTask() {
            this.$emit('update:task', this.thisTask)
        },
        searchText() {
            this.filterValues()
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        ...mapState(useLoopAI, ['tasks', 'config']),
        ...mapState(useTheme, ['color', 'gradient']),
        flatFormatedOperators() {
            let operators = []
            for (let key in this.groupOperators) {
                operators.push(...this.groupOperators[key].items)
            }
            return operators
        },
        totalNumVisible() {
            return this.tasks.filter((item) => item.show).length
        }
    },
    mounted() {
        this.getTasks()
    },
    methods: {
        ...mapActions(useLoopAI, ['getTasks', 'getConfigs']),
        async getTaskList() {
            if (!this.lock.task) return
            this.lock.task = false
            await this.getTasks()
            this.lock.task = true
        },
        filterValues() {
            this.tasks.forEach((item) => {
                item.show = this.isSearchShowItem(item)
            })
        },
        isSearchShowItem(item) {
            let searchText = this.searchText.toLowerCase()
            return item.name.toLowerCase().includes(searchText)
        },
        async selectTask(item) {
            console.log(item)
            this.thisTask = item
        },
        delTask(item) {
            if (!item) return
            this.$infoBox(this.local('Are you sure to delete this task?'), {
                status: 'error',
                confirm: () => {
                    this.$api.task.delTask(item.id).then((res) => {
                        if (res.code === 200) {
                            this.getTaskList()
                            this.thisTask = null
                        } else
                            this.$barWarning(res.msg || this.local('Delete task failed'), {
                                status: 'warning'
                            })
                    })
                }
            })
        },
        confirmTask(item) {
            if (!this.thisTask) this.thisTask = item
        },
        showRightMenu($event, item) {
            this.currentContextItem = item
            $event.preventDefault()
            $event.stopPropagation()
            this.$refs.rightMenu.rightClick($event, document.body)
        }
    }
}
</script>

<style lang="scss">
.lp-task-container {
    position: relative;
    width: 100%;
    height: 100%;
    background: rgba(250, 250, 250, 0.3);
    border: rgba(120, 120, 120, 0.1) solid thin;
    display: flex;
    flex-direction: column;
    backdrop-filter: blur(10px);

    hr {
        margin: 10px 0px;
        border: none;
        border-top: rgba(120, 120, 120, 0.1) solid thin;
    }

    .lp-task-header {
        position: relative;
        width: 100%;
        height: 50px;
        margin-top: 20px;
        padding: 15px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 15px;

        .left-block {
            @include Vcenter;

            gap: 15px;
        }

        .title {
            @include color-dataflow-title;

            font-size: 18px;
            font-weight: bold;
            user-select: none;
        }

        .logo {
            width: 25px;
            height: 25px;
        }
    }

    .lp-task-content {
        position: relative;
        width: 100%;
        height: 10px;
        flex: 1;
        display: flex;
        flex-direction: column;

        .search-block {
            position: relative;
            width: 100%;
            height: auto;
            padding: 0px 10px;
            display: flex;
            flex-direction: column;

            .task-search-box {
                width: 100%;
                height: 40px;
            }

            .search-result-info {
                @include Vcenter;

                height: 35px;
                margin-top: 5px;
                padding: 0px 10px;
                background: rgba(239, 239, 239, 1);
                border-radius: 8px;
                font-size: 12px;
                font-weight: 400;
                color: var(--node-status-color);

                .search-text {
                    margin-left: 5px;
                    font-size: 12px;
                    font-weight: 400;
                    color: rgba(0, 90, 158, 1);
                }
            }
        }

        .task-list-loading {
            position: absolute;
            top: 150px;
            left: 50%;
            transform: translate(-50%, -50%);
        }

        .task-list-block {
            position: relative;
            width: 100%;
            height: 10px;
            flex: 1;
            margin-top: 5px;
            overflow: overlay;

            .task-item {
                position: relative;
                width: 100%;
                height: 80px;
                padding: 0px 10px;
                display: flex;
                flex-direction: column;
                transition: background 0.3s;

                &:hover {
                    background: rgba(227, 231, 251, 0.6);

                    .task-item-main {
                        .content-block {
                            .task-name {
                                color: rgba(0, 90, 158, 1);
                            }
                        }
                    }
                }

                &:active {
                    background: rgba(227, 231, 251, 0.8);
                }

                &.choosen {
                    background: rgba(227, 231, 251, 1);
                }

                .task-item-main {
                    position: relative;
                    width: 100%;
                    flex: 1;
                    display: flex;
                    align-items: center;

                    .main-icon {
                        @include HcenterVcenter;

                        position: relative;
                        width: 40px;
                        height: 40px;
                        flex-shrink: 0;
                        background: linear-gradient(90deg,
                                rgba(73, 131, 251, 1) 0%,
                                rgba(100, 161, 252, 1) 100%);
                        border: 1px solid rgba(120, 120, 120, 0.1);
                        border-radius: 8px;
                        color: whitesmoke;
                        box-shadow: 0px 1px 2px rgba(0, 0, 0, 0.1);
                    }

                    .content-block {
                        @include HstartC;

                        position: relative;
                        width: 50px;
                        flex: 1;
                        height: 100%;
                        padding: 10px;
                        line-height: 2;
                        user-select: none;

                        .row-item {
                            @include HbetweenVcenter;

                            position: relative;
                            width: 100%;
                        }

                        .task-name {
                            @include nowrap;

                            position: relative;
                            width: 100%;
                            font-size: 12.8px;
                            font-weight: bold;
                            color: rgba(58, 61, 79, 1);
                            transition: color 0.3s;
                        }

                        .task-info {
                            @include nowrap;

                            margin-right: 5px;
                            font-size: 10px;
                            color: rgba(120, 120, 120, 1);
                        }
                    }
                }

                hr {
                    margin-top: 5px;
                }
            }
        }
    }
}

.task-slide-enter-active {
    transition: all 0.6s ease-out;
}

.task-slide-leave-active {
    transition: all 0.3s;
}

.task-slide-enter-from,
.task-slide-leave-to {
    width: 0px;
    max-width: 0px;
}

.task-slide-enter-to,
.task-slide-leave-from {
    width: 100%;
    max-width: 100%;
}
</style>
