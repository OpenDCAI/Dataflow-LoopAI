<template>
    <div class="lp-serving-container">
        <div class="major-container">
            <div class="title-block">
                <p class="main-title">{{ local('Global Config') }}</p>
                <div class="right-block">
                    <fv-button
                        theme="dark"
                        icon="Go"
                        :is-box-shadow="true"
                        :background="gradient"
                        :disabled="!lock.update"
                        border-radius="6"
                        style="width: 90px"
                        @click="updateConfig"
                    >
                        {{ local('Update') }}
                    </fv-button>
                    <fv-button
                        icon="Refresh"
                        :is-box-shadow="true"
                        border-radius="6"
                        :disabled="!lock.update"
                        style="width: 90px"
                        @click="reset"
                    >
                        {{ local('Reset') }}
                    </fv-button>
                </div>
            </div>
            <div class="content-block">
                <fv-Collapse
                    :model-value="true"
                    class="serving-item"
                    icon="DialShape3"
                    :title="local('Starter')"
                    :content="local('Starter Config.')"
                    :max-height="'auto'"
                >
                    <template v-slot:default>
                        <hr />
                        <div v-if="config.starter" v-for="(val, key) in config.starter">
                            <div class="serving-item-row column">
                                <p class="serving-item-light-title">{{ local(key) }}</p>
                                <value-input
                                    :model-value="val"
                                    :name="key"
                                    :lock="lock.update"
                                ></value-input>
                            </div>
                            <hr />
                        </div>
                    </template>
                </fv-Collapse>
                <fv-Collapse
                    :model-value="true"
                    class="serving-item"
                    icon="DialShape3"
                    :title="local('RAG')"
                    :content="local('RAG Config.')"
                    :max-height="'auto'"
                >
                    <template v-slot:default>
                        <hr />
                        <div v-if="config.rag" v-for="(val, key) in config.rag">
                            <div class="serving-item-row column">
                                <p class="serving-item-light-title">{{ local(key) }}</p>
                                <value-input
                                    :model-value="val"
                                    :name="key"
                                    :lock="lock.update"
                                ></value-input>
                            </div>
                            <hr />
                        </div>
                    </template>
                </fv-Collapse>
                <fv-Collapse
                    :model-value="true"
                    class="serving-item"
                    icon="DialShape3"
                    :title="local('Default States')"
                    :content="local('Default States Config.')"
                    :max-height="'auto'"
                >
                    <template v-slot:default>
                        <hr />
                        <div
                            v-if="config.default_states"
                            v-for="(val, key) in config.default_states"
                        >
                            <div class="serving-item-row column">
                                <p class="serving-item-light-title">{{ local(key) }}</p>
                                <value-input
                                    :model-value="val"
                                    :name="key"
                                    :lock="lock.update"
                                ></value-input>
                            </div>
                            <hr />
                        </div>
                    </template>
                </fv-Collapse>
            </div>
        </div>
    </div>
</template>

<script>
import { mapState, mapActions } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'
import { useTheme } from '@/stores/theme'
import { useLoopAI } from '@/stores/loopAI'

import valueInput from '@/components/manage/config/valueInput.vue'

export default {
    components: {
        valueInput
    },
    data() {
        return {
            formatValues: {
                str: (val) => val.toString(),
                int: (val) => parseInt(val),
                Any: (val) => val.toString()
            },
            lock: {
                update: true
            }
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        ...mapState(useTheme, ['theme', 'color', 'gradient']),
        ...mapState(useLoopAI, ['configId', 'config'])
    },
    mounted() {
        this.getConfigs()
    },
    methods: {
        ...mapActions(useLoopAI, ['getConfigs']),
        async updateConfig() {
            if (!this.lock.update) return
            this.lock.update = false
            await this.$api.config
                .updateConfig({
                    id: this.id,
                    config: JSON.stringify(this.config)
                })
                .then((res) => {
                    if (res.code === 200) {
                        this.$barWarning(this.local('Update Config Success.'), {
                            status: 'correct'
                        })
                    }
                })
            this.lock.update = true
        },
        valueBuilder(item) {
            let type = item.type
            return this.formatValues[type](item.value)
        },
        reset() {
            for (let key in this.config) {
                if (this.config[key]) {
                    for (let param_key in this.config[key]) {
                        this.config[key][param_key].value =
                            this.config[key][param_key].default_value === null
                                ? ''
                                : this.config[key][param_key].default_value
                    }
                }
            }
        }
    }
}
</script>

<style lang="scss">
.lp-serving-container {
    position: relative;
    width: 100%;
    height: 100%;
    background-color: rgba(241, 241, 241, 1);
    display: flex;
    justify-content: center;

    .major-container {
        position: relative;
        width: 100%;
        max-width: 1200px;
        height: 100%;
        box-sizing: border-box;
        display: flex;
        flex-direction: column;

        .title-block {
            @include HbetweenVcenter;

            position: absolute;
            width: 100%;
            padding: 15px;
            padding-top: 30px;
            z-index: 1;
            backdrop-filter: blur(20px);

            .main-title {
                font-size: 28px;
                font-weight: 400;
                color: rgba(26, 26, 26, 1);
            }

            .right-block {
                @include HendVcenter;

                width: 10px;
                flex: 1;
                gap: 5px;
            }
        }

        .content-block {
            position: relative;
            width: 100%;
            height: 100%;
            gap: 5px;
            padding: 15px;
            padding-top: 100px;
            display: flex;
            flex-direction: column;
            overflow: overlay;

            .serving-item {
                flex-shrink: 0;

                .collapse-item-content {
                    position: relative;
                    height: auto;
                    transition: all 0.3s;
                }

                .serving-item-title {
                    margin: 5px 0px;
                    font-size: 13.8px;
                    font-weight: bold;
                    color: rgba(123, 139, 209, 1);
                    user-select: none;
                }

                .serving-item-light-title {
                    margin: 5px 0px;
                    font-size: 12px;
                    color: rgba(95, 95, 95, 1);
                    user-select: none;
                }

                .serving-item-info {
                    margin: 5px 0px;
                    font-size: 12px;
                    color: rgba(120, 120, 120, 1);
                    user-select: none;
                }

                .serving-item-std-info {
                    font-size: 13.8px;
                    color: rgba(27, 27, 27, 1);
                    user-select: none;
                }

                .serving-item-bold-info {
                    margin: 5px 0px;
                    font-size: 16px;
                    font-weight: bold;
                    color: rgba(27, 27, 27, 1);
                    user-select: none;
                }

                .serving-item-p-block {
                    position: relative;
                    width: 100%;
                    height: auto;
                    padding: 15px 0px;
                    box-sizing: border-box;
                    line-height: 3;
                    display: flex;
                    flex-direction: column;
                }

                .serving-item-row {
                    position: relative;
                    width: 100%;
                    padding: 0px 42px;
                    flex-wrap: wrap;
                    box-sizing: border-box;
                    display: flex;
                    align-items: center;

                    &.no-pad {
                        padding: 0px;
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
    }

    .rainbow {
        @include color-rainbow;

        color: black;
    }

    .ring-animation {
        animation: ring-rotate 1s linear infinite;
    }

    @keyframes ring-rotate {
        0% {
            transform: rotate(0deg);
        }
        100% {
            transform: rotate(360deg);
        }
    }
}
</style>
