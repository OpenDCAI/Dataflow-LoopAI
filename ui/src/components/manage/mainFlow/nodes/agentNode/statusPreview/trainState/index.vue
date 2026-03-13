<template>
    <div v-show="oriStateObj" class="train-state">
        <p class="info-title" style="margin-bottom: 5px; font-size: 12px">
            {{ local('Terminal Logs') }}
        </p>
        <div class="terminal-logs" @wheel.stop>
            <p v-for="(line, id) in loglines" :key="id">{{ line }}</p>
        </div>
        <div class="chart-container" @wheel.stop>
            <lineChart
                v-for="(attr, idx) in chartDataAttrs"
                class="chart-item"
                :key="idx"
                :labels="chartLabels"
                :data="statusList.map((item) => item[attr])"
                :title="attr"
                :dataLabel="`${attr}`"
            />
        </div>
    </div>
</template>

<script>
import { mapState } from 'pinia'
import { useLoopAI } from '@/stores/loopAI'
import { useAppConfig } from '@/stores/appConfig'

import lineChart from './lineChart.vue'

export default {
    components: {
        lineChart
    },
    props: {
        running: {
            type: Boolean,
            default: false
        }
    },
    data() {
        return {
            oriStateObj: null,
            timer: null,
            loglines: [],
            statusList: []
        }
    },
    watch: {
        running: {
            handler(val) {
                if (val) {
                    this.timerInit()
                } else clearInterval(this.timer)
            },
            immediate: true
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        ...mapState(useLoopAI, ['taskStatus']),
        state() {
            return this.taskStatus.state
        },
        custom_info() {
            return this.taskStatus.custom_info || {}
        },
        chartLabels() {
            return this.statusList.map((item) => item.step)
        },
        chartDataAttrs() {
            let arr = []
            for (let key in this.statusList[0]) {
                if (key === 'step') continue
                if (typeof this.statusList[0][key] === 'number') {
                    arr.push(key)
                }
            }
            return arr
        }
    },
    mounted() {
        setTimeout(() => {
            this.getTrainState()
        }, 300)
    },
    methods: {
        timerInit() {
            clearInterval(this.timer)
            this.timer = setInterval(() => {
                if (!this.running) {
                    clearInterval(this.timer)
                }
                this.getTrainState()
            }, 3000)
        },
        getTrainState() {
            try {
                let task_id = this.state.task_id
                let output_dir = this.state.output_dir
                let trainer_task_id =
                    this.custom_info['TrainerAgent.training_execution_node_wrapper'].data.task_id
                this.$api.task
                    .getTrainStatus(output_dir, task_id, trainer_task_id)
                    .then((res) => {
                        if (res.code === 200) {
                            this.oriStateObj = res.data
                            this.processState()
                        }
                    })
                    .catch((err) => {
                        console.log(err)
                    })
            } catch (err) {}
        },
        processState() {
            let metrics = this.oriStateObj.metrics || []
            this.loglines = []
            this.statusList = []
            metrics.forEach((item) => {
                if (item.log_line) {
                    this.loglines.push(item.log_line)
                }
                if (item.loss != undefined && item.step != undefined) {
                    this.statusList.push(item)
                }
            })
        }
    },
    beforeDestroy() {
        clearInterval(this.timer)
    }
}
</script>

<style lang="scss">
.train-state {
    position: relative;
    width: 250px;
    height: 100%;
    padding: 5px 0px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    overflow: hidden;

    .terminal-logs {
        position: relative;
        width: 100%;
        height: 120px;
        padding: 5px;
        background: rgba(45, 45, 45, 1);
        font-size: 10px;
        color: whitesmoke;
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 8px;
        white-space: nowrap;
        overflow: overlay;
        box-shadow: 0px 0px 10px rgba(0, 0, 0, 0.1);
    }

    .chart-container {
        position: relative;
        width: 100%;
        height: 20px;
        flex: 1;
        margin-top: 5px;
        padding: 5px;
        gap: 5px;
        background: rgba(250, 250, 250, 0.6);
        font-size: 10px;
        color: whitesmoke;
        border: 1px solid rgba(120, 120, 120, 0.1);
        border-radius: 8px;
        white-space: nowrap;
        display: flex;
        flex-direction: column;
        overflow: overlay;

        .chart-item {
            position: relative;
            width: 100%;
            height: auto;
            flex: 1;
            background: rgba(255, 255, 255, 1);
            border-radius: 12px;
        }
    }
}
</style>
