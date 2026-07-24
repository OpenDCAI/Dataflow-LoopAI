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
            if (!this.statusList.length) return []
            const keys = new Set()
            this.statusList.forEach((item) => {
                Object.keys(item).forEach((key) => {
                    if (key !== 'step' && typeof item[key] === 'number') keys.add(key)
                })
            })
            const preferred = [
                'loss',
                'eval_loss',
                'reward',
                'val_score',
                'policy_loss',
                'accuracy',
                'learning_rate'
            ]
            return [
                ...preferred.filter((key) => keys.has(key)),
                ...Array.from(keys).filter((key) => !preferred.includes(key))
            ].slice(0, 8)
        }
    },
    mounted() {
        setTimeout(() => {
            this.getTrainState()
        }, 1000)
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
                let trainerState = this.state.trainer || {}
                let trainerRuntime = (this.taskStatus.node_status || []).find(
                    (item) => item.node_name === 'trainer'
                )
                let trainer_task_id =
                    trainerRuntime?.version ||
                    trainerState.trainer_version_id ||
                    trainerState.trainer_task_id ||
                    trainerState.trainer_training_task_id ||
                    this.custom_info['trainer.trainer.run']?.version_id ||
                    ''
                if (!trainer_task_id) return
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
                const hasNumericMetric = Object.keys(item).some(
                    (key) => key !== 'step' && typeof item[key] === 'number'
                )
                if (item.step != undefined && hasNumericMetric) {
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
