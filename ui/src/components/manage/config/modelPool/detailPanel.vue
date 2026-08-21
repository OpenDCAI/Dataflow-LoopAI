<template>
    <base-panel
        :model-value="modelValue"
        :title="local('Model Pool')"
        width="min(980px, 92%)"
        height="72%"
        theme="light"
        :is-footer="false"
        :teleport="true"
        @update:model-value="$emit('update:modelValue', $event)"
    >
        <template v-slot:content>
            <div class="model-pool-detail">
                <div class="model-chart-grid">
                    <div class="model-chart-card">
                        <p class="model-chart-title">{{ local('Requests') }}</p>
                        <base-chart type="bar" :chart-data="requestsChart" :options="chartOptions"></base-chart>
                    </div>
                    <div class="model-chart-card">
                        <p class="model-chart-title">{{ local('Errors') }}</p>
                        <base-chart type="bar" :chart-data="errorsChart" :options="chartOptions"></base-chart>
                    </div>
                    <div class="model-chart-card">
                        <p class="model-chart-title">{{ local('Latency') }}</p>
                        <base-chart type="bar" :chart-data="latencyChart" :options="chartOptions"></base-chart>
                    </div>
                    <div class="model-chart-card">
                        <p class="model-chart-title">{{ local('Tokens') }}</p>
                        <base-chart type="doughnut" :chart-data="tokensChart" :options="doughnutOptions"></base-chart>
                    </div>
                </div>
                <div class="model-detail-row" v-for="model in modelPoolModels" :key="model.name">
                    <div class="model-detail-main">
                        <p class="model-detail-title">
                            <span class="model-detail-dot" :class="healthClass(model)"></span>
                            {{ model.tier }} / {{ model.name }}
                        </p>
                        <p class="model-detail-sub">{{ model.model_name }} / {{ model.wire_api }}</p>
                        <p class="model-detail-sub">{{ model.base_url }}</p>
                    </div>
                    <div class="model-detail-stats">
                        <p>{{ healthText(model) }}</p>
                        <p>{{ model.stats?.requests || 0 }} req</p>
                        <p>{{ model.stats?.avg_latency_ms || '-' }} ms</p>
                        <p>{{ model.stats?.usage?.total_tokens || 0 }} tokens</p>
                    </div>
                    <pre v-if="model.stats?.last_error" class="model-error">{{ model.stats.last_error }}</pre>
                </div>
            </div>
        </template>
    </base-panel>
</template>

<script>
import { mapState } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'
import basePanel from '@/components/general/basePanel.vue'
import baseChart from '@/components/manage/obtainerLake/baseChart.vue'

export default {
    name: 'ModelPoolDetailPanel',
    components: { basePanel, baseChart },
    props: {
        modelValue: { type: Boolean, default: false },
        config: { type: Object, required: true },
        status: { type: Object, default: () => ({}) }
    },
    emits: ['update:modelValue'],
    computed: {
        ...mapState(useAppConfig, ['local']),
        modelPoolValue() {
            return this.config.system?.model?.value || null
        },
        editableModelPool() {
            const pool = this.modelPoolValue?.pool
            return Array.isArray(pool) ? pool : []
        },
        statusModelPool() {
            return this.status.models || []
        },
        modelPoolModels() {
            return this.statusModelPool.length ? this.statusModelPool : this.editableModelPool
        },
        modelChartLabels() {
            return this.modelPoolModels.map((model) => `${model.tier || 'model'}:${model.name || model.model_name}`)
        },
        requestsChart() {
            return this.buildBarChart(
                [this.local('Requests')],
                [this.modelPoolModels.map((model) => model.stats?.requests || 0)],
                ['rgba(87, 99, 206, 0.76)']
            )
        },
        errorsChart() {
            return this.buildBarChart(
                [this.local('Errors')],
                [this.modelPoolModels.map((model) => model.stats?.errors || 0)],
                ['rgba(111, 91, 195, 0.76)']
            )
        },
        latencyChart() {
            return this.buildBarChart(
                [this.local('Avg Latency')],
                [this.modelPoolModels.map((model) => model.stats?.avg_latency_ms || 0)],
                ['rgba(239, 192, 41, 0.76)']
            )
        },
        tokensChart() {
            return {
                labels: this.modelChartLabels,
                datasets: [
                    {
                        data: this.modelPoolModels.map((model) => model.stats?.usage?.total_tokens || 0),
                        backgroundColor: [
                            'rgba(87, 99, 206, 0.78)',
                            'rgba(48, 156, 11, 0.78)',
                            'rgba(239, 192, 41, 0.78)',
                            'rgba(78, 173, 155, 0.78)',
                            'rgba(210, 133, 158, 0.78)'
                        ],
                        borderWidth: 0
                    }
                ]
            }
        },
        chartOptions() {
            return {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false }
                },
                scales: {
                    x: {
                        ticks: { color: 'rgba(75, 75, 75, 1)', maxRotation: 0, autoSkip: true },
                        grid: { display: false }
                    },
                    y: {
                        beginAtZero: true,
                        ticks: { color: 'rgba(75, 75, 75, 1)', precision: 0 },
                        grid: { color: 'rgba(215, 220, 230, 0.7)' }
                    }
                }
            }
        },
        doughnutOptions() {
            return {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: { boxWidth: 10, color: 'rgba(75, 75, 75, 1)' }
                    }
                },
                cutout: '58%'
            }
        }
    },
    methods: {
        buildBarChart(labels, datasets, colors) {
            return {
                labels: this.modelChartLabels,
                datasets: datasets.map((data, index) => ({
                    label: labels[index],
                    data,
                    backgroundColor: colors[index],
                    borderRadius: 4,
                    maxBarThickness: 36
                }))
            }
        },
        hasProbeResult(model) {
            const probe = model?.probe || {}
            return Boolean(probe.checked_at || probe.error || probe.chat || probe.responses)
        },
        hasProbeFailure(model) {
            const probe = model?.probe || {}
            if (probe.error) return true
            return ['chat', 'responses'].some((key) => {
                const result = probe[key]
                if (!result) return false
                if (result.ok) return false
                return Boolean(result.error || result.status_code || result.latency_ms)
            })
        },
        healthText(model) {
            const selected = model.probe?.selected_wire_api
            if (selected) return `${this.local('Online')} 路 ${selected}`
            if (model.probe?.chat?.ok) return `${this.local('Online')} 路 chat`
            if (model.probe?.responses?.ok) return `${this.local('Online')} 路 responses`
            if (this.hasProbeFailure(model)) return this.local('Unavailable')
            return this.local('Not Probed')
        },
        healthClass(model) {
            const selected = model.probe?.selected_wire_api
            if (selected || model.probe?.chat?.ok || model.probe?.responses?.ok) return 'healthy'
            if (model.enabled === false) return 'disabled'
            if (this.hasProbeFailure(model) || this.hasProbeResult(model)) return 'unhealthy'
            return 'unknown'
        }
    }
}
</script>

<style lang="scss" scoped>
.model-pool-detail {
    width: 100%;
    height: 100%;
    overflow: auto;
    display: flex;
    flex-direction: column;
    gap: 10px;
}
.model-chart-grid {
    width: 100%;
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px;
    flex-shrink: 0;
}
.model-chart-card {
    min-width: 0;
    height: 240px;
    padding: 12px;
    box-sizing: border-box;
    border: 1px solid var(--lp-line);
    border-radius: 6px;
    background: var(--lp-surface);
    display: flex;
    flex-direction: column;
}
.model-chart-title {
    margin-bottom: 8px;
    font-size: 13px;
    font-weight: 600;
    color: var(--lp-text);
}
.model-chart-card :deep(.ol-chart-shell) {
    flex: 1;
    min-height: 0;
}
.model-detail-row {
    width: 100%;
    padding: 12px;
    box-sizing: border-box;
    border: 1px solid var(--lp-line);
    border-radius: 6px;
    background: var(--lp-surface);
    display: grid;
    grid-template-columns: minmax(0, 1fr) 260px;
    gap: 10px;
}
.model-detail-title {
    font-size: 14px;
    font-weight: 600;
    color: var(--lp-text);
    display: flex;
    align-items: center;
    gap: 7px;
}
.model-detail-dot {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: var(--lp-raised);
    flex-shrink: 0;
}
.model-detail-dot.healthy {
    background: rgba(38, 166, 112, 1);
    box-shadow: 0 0 0 3px rgba(38, 166, 112, 0.14);
}
.model-detail-dot.unhealthy {
    background: rgba(205, 76, 76, 1);
    box-shadow: 0 0 0 3px rgba(205, 76, 76, 0.12);
}
.model-detail-dot.disabled {
    background: var(--lp-raised);
}
.model-detail-sub {
    margin-top: 4px;
    font-size: 12px;
    color: var(--lp-text);
    word-break: break-all;
}
.model-detail-stats {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 6px;
}
.model-detail-stats p {
    padding: 6px;
    border-radius: 6px;
    background: var(--lp-surface);
    font-size: 12px;
    color: var(--lp-text);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.model-error {
    grid-column: 1 / -1;
    margin: 0;
    padding: 8px;
    border-radius: 6px;
    background: var(--lp-surface);
    color: rgba(126, 32, 32, 1);
    font-size: 12px;
    white-space: pre-wrap;
}
@media (max-width: 760px) {
    .model-chart-grid {
        grid-template-columns: 1fr;
    }
    .model-detail-row {
        grid-template-columns: 1fr;
    }
}
</style>
