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
                    :title="local('System')"
                    :content="local('System Config.')"
                    :max-height="'auto'"
                >
                    <template v-slot:default>
                        <hr />
                        <model-pool-panel
                            v-if="modelPoolAvailable"
                            :config="config"
                            :status="modelPoolStatus"
                            :probe-loading="modelPoolProbeLoading"
                            :probe-message="modelPoolProbeMessage"
                            @probe="handleProbe"
                            @show-detail="show.modelPool = true"
                        ></model-pool-panel>
                        <hr v-if="modelPoolAvailable" />
                        <div
                            v-if="config.system"
                            v-for="entry in systemConfigEntries"
                            :key="entry[0]"
                        >
                            <div class="serving-item-row column">
                                <p class="serving-item-light-title">{{ local(entry[0]) }}</p>
                                <value-input
                                    :model-value="entry[1]"
                                    :name="entry[0]"
                                    :lock="lock.update"
                                    @select-dataset="handleSelectDataset(entry[1])"
                                ></value-input>
                            </div>
                            <hr />
                        </div>
                    </template>
                </fv-Collapse>
                <p class="lp-serving-title">{{ local('States') }}</p>
                <div v-for="(state_val, state_key) in config.states">
                    <fv-Collapse
                        :model-value="true"
                        class="serving-item"
                        icon="DialShape3"
                        :title="state_key"
                        :content="local('State Config')"
                        :max-height="'auto'"
                    >
                        <template v-slot:default>
                            <hr />
                            <div v-for="(val, key) in state_val">
                                <div class="serving-item-row column">
                                    <div class="serving-item-row no-pad" style="gap: 5px">
                                        <p class="serving-item-light-title">
                                            {{ local(key) }}
                                        </p>
                                        <fv-callout
                                            v-if="val.description"
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
                                                <p style="font-size: 13px">{{ val.description }}</p>
                                            </template>
                                        </fv-callout>
                                    </div>
                                    <value-input
                                        :model-value="val"
                                        :name="key"
                                        :lock="lock.update"
                                        @select-dataset="handleSelectDataset(val)"
                                    ></value-input>
                                </div>
                                <hr />
                            </div>
                        </template>
                    </fv-Collapse>
                </div>
            </div>
        </div>
        <resource-panel
            v-model="show.dataset"
            :title="local('Dataset')"
            mode="read"
            @confirm="handleDatasetConfirm"
        ></resource-panel>
        <model-pool-detail-panel
            v-model="show.modelPool"
            :config="config"
            :status="modelPoolStatus"
        ></model-pool-detail-panel>
    </div>
</template>

<script>
import { mapState, mapActions } from 'pinia'
import { getBaseURL } from '@/axios/config'
import { useAppConfig } from '@/stores/appConfig'
import { useTheme } from '@/stores/theme'
import { useLoopAI } from '@/stores/loopAI'

import ModelPoolPanel from '@/components/manage/config/modelPool/index.vue'
import ModelPoolDetailPanel from '@/components/manage/config/modelPool/detailPanel.vue'
import valueInput from '@/components/manage/config/valueInput/index.vue'
import resourcePanel from '@/components/manage/mainFlow/panels/resourcePanel/index.vue'

export default {
    components: {
        ModelPoolPanel,
        ModelPoolDetailPanel,
        valueInput,
        resourcePanel
    },
    data() {
        return {
            formatValues: {
                str: (val) => val.toString(),
                int: (val) => parseInt(val),
                Any: (val) => val.toString()
            },
            currentSelectItem: null,
            lock: {
                update: true
            },
            show: {
                dataset: false,
                modelPool: false
            },
            modelPoolStatus: {},
            modelPoolProbeLoading: false,
            modelPoolProbeMessage: '',
            tierOptions: ['high', 'medium', 'low'],
            wireApiOptions: ['auto', 'responses', 'chat'],
            responseFormatOptions: [
                { key: '', text: 'auto' },
                { key: 'responses', text: 'responses' },
                { key: 'chat', text: 'chat/completions' }
            ]
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        ...mapState(useTheme, ['theme', 'color', 'gradient']),
        ...mapState(useLoopAI, ['configId', 'config']),
        systemConfigEntries() {
            const hidden = new Set([
                'model',
                'starter_api_key',
                'starter_model_path',
                'starter_model_name',
                'starter_base_url',
                'starter_maxworker',
                'starter_wire_api',
                'codex_api_key',
                'codex_model',
                'codex_base_url',
                'codex_maxworker',
                'codex_wire_api',
                'codex_model_provider',
                'codex_api_key_env_key',
                'codex_provider_name',
                'codex_supports_websockets'
            ])
            return Object.entries(this.config.system || {}).filter(([key]) => !hidden.has(key))
        },
        modelPoolValue() {
            return this.config.system?.model?.value || null
        },
        modelPoolConfig() {
            return (
                this.modelPoolValue || {
                    proxy_base_url: '',
                    proxy_api_key: 'loopai-local-proxy',
                    default_tier: 'medium',
                    pool: []
                }
            )
        },
        editableModelPool() {
            if (!Array.isArray(this.modelPoolConfig.pool)) this.modelPoolConfig.pool = []
            return this.modelPoolConfig.pool
        },
        statusModelPool() {
            return this.modelPoolStatus.models || []
        },
        modelPoolAvailable() {
            return Boolean(this.config)
        },
        modelPoolProxyBaseUrl() {
            return this.modelPoolConfig.proxy_base_url || ''
        },
        modelPoolModels() {
            return this.statusModelPool.length ? this.statusModelPool : this.editableModelPool
        },
        modelPoolOverview() {
            const total = this.modelPoolModels.length
            const online = this.modelPoolModels.filter(
                (model) => this.healthClass(model) === 'healthy'
            ).length
            const unhealthy = this.modelPoolModels.filter(
                (model) => this.healthClass(model) === 'unhealthy'
            ).length
            const requests = this.modelPoolModels.reduce(
                (sum, model) => sum + (model.stats?.requests || 0),
                0
            )
            const errors = this.modelPoolModels.reduce(
                (sum, model) => sum + (model.stats?.errors || 0),
                0
            )
            const tokens = this.modelPoolModels.reduce(
                (sum, model) => sum + (model.stats?.usage?.total_tokens || 0),
                0
            )
            const latencyItems = this.modelPoolModels
                .map((model) => Number(model.stats?.avg_latency_ms || 0))
                .filter((value) => value > 0)
            const avgLatency = latencyItems.length
                ? Math.round(
                      latencyItems.reduce((sum, value) => sum + value, 0) / latencyItems.length
                  )
                : 0
            let statusClass = 'unknown'
            let statusText = this.local('Not Probed')
            if (total && online === total) {
                statusClass = 'healthy'
                statusText = this.local('All Online')
            } else if (online > 0) {
                statusClass = 'warning'
                statusText = this.local('Partial Online')
            } else if (unhealthy > 0) {
                statusClass = 'unhealthy'
                statusText = this.local('Unavailable')
            }
            return {
                total,
                online,
                unhealthy,
                requests,
                errors,
                tokens,
                avgLatency,
                statusClass,
                statusText
            }
        },
        modelNodeRows() {
            return this.modelPoolModels.map((model, index) => {
                const healthClass = this.healthClass(model)
                const selected = model.probe?.selected_wire_api
                const api = selected || model.wire_api || 'auto'
                const latency = model.stats?.avg_latency_ms
                    ? `${model.stats.avg_latency_ms} ms`
                    : '- ms'
                return {
                    key: model.name || model.model_name || index,
                    name: model.name || model.model_name || 'model',
                    modelName: model.model_name || model.name || '-',
                    tier: model.tier || 'medium',
                    healthClass,
                    statusText: this.healthText(model),
                    api,
                    latency,
                    requests: model.stats?.requests || 0,
                    errors: model.stats?.errors || 0,
                    tokens: model.stats?.usage?.total_tokens || 0
                }
            })
        },
        modelSelectOptions() {
            const source = this.editableModelPool.length
                ? this.editableModelPool
                : this.modelPoolModels
            const options = source.map((model) => {
                const tier = model.tier || 'medium'
                const label = `${tier} · ${model.name || model.model_name || 'model'}`
                return {
                    key: model.name || model.model_name || tier,
                    text: label,
                    model
                }
            })
            if (!options.length) {
                const legacyName =
                    this.config.system?.starter_model_name?.value ||
                    this.config.system?.starter_model_path?.value ||
                    this.config.system?.codex_model?.value
                if (legacyName) {
                    options.push({
                        key: legacyName,
                        text: `medium · ${legacyName}`,
                        model: { tier: 'medium', name: legacyName, model_name: legacyName }
                    })
                }
            }
            if (!options.length) {
                options.push({
                    key: '__empty_model__',
                    text: '-',
                    model: {
                        tier: this.modelPoolConfig.default_tier || 'medium',
                        name: '',
                        model_name: ''
                    },
                    placeholder: true
                })
            }
            return options
        },
        selectedModelOption: {
            get() {
                const defaultTier = this.modelPoolConfig.default_tier
                const starterModel =
                    this.config.system?.starter_model_name?.value ||
                    this.config.system?.starter_model_path?.value
                const matched =
                    this.modelSelectOptions.find(
                        (option) => option.model?.model_name === starterModel
                    ) ||
                    this.modelSelectOptions.find((option) => option.model?.name === starterModel) ||
                    this.modelSelectOptions.find((option) => option.model?.tier === defaultTier) ||
                    this.modelSelectOptions[0]
                return matched || null
            },
            set(option) {
                if (!option || option.placeholder) return
                const model = option.model || {}
                if (this.config.system?.model?.value && model.tier) {
                    this.config.system.model.value.default_tier = model.tier
                }
                this.setSystemValue(
                    'starter_model_name',
                    model.model_name || model.name || option.key
                )
                this.setSystemValue(
                    'starter_model_path',
                    model.model_name || model.name || option.key
                )
                if (model.base_url) this.setSystemValue('starter_base_url', model.base_url)
                if (model.api_key && !String(model.api_key).startsWith('env:')) {
                    this.setSystemValue('starter_api_key', model.api_key)
                }
            }
        },
        codexModelOption: {
            get() {
                const codexModel = this.config.system?.codex_model?.value
                const matched =
                    this.modelSelectOptions.find((option) => option.model?.name === codexModel) ||
                    this.modelSelectOptions.find(
                        (option) => option.model?.model_name === codexModel
                    ) ||
                    this.modelSelectOptions.find((option) => option.model?.name === 'codex') ||
                    this.modelSelectOptions[0]
                return matched || null
            },
            set(option) {
                if (!option || option.placeholder) return
                const model = option.model || {}
                this.setSystemValue('codex_model', model.name || model.model_name || option.key)
                this.setSystemValue('codex_wire_api', 'responses')
            }
        },
        modelPoolTiers() {
            const tiers = ['high', 'medium', 'low']
            return tiers.map((tier) => {
                const models = this.modelPoolModels.filter((model) => model.tier === tier)
                const healthy = models.filter((model) => {
                    const selected = model.probe?.selected_wire_api
                    return selected || model.probe?.chat?.ok || model.probe?.responses?.ok
                }).length
                const requests = models.reduce(
                    (sum, model) => sum + (model.stats?.requests || 0),
                    0
                )
                const errors = models.reduce((sum, model) => sum + (model.stats?.errors || 0), 0)
                const total = models.length
                return {
                    name: tier,
                    total,
                    healthy,
                    requests,
                    errors,
                    rate: total ? Math.round((healthy / total) * 100) : 0
                }
            })
        },
        modelChartLabels() {
            return this.modelPoolModels.map(
                (model) => `${model.tier || 'model'}:${model.name || model.model_name}`
            )
        },
        requestsChart() {
            return this.buildBarChart(
                [this.local('Requests')],
                [this.modelPoolModels.map((model) => model.stats?.requests || 0)],
                ['rgba(45, 125, 210, 0.76)']
            )
        },
        errorsChart() {
            return this.buildBarChart(
                [this.local('Errors')],
                [this.modelPoolModels.map((model) => model.stats?.errors || 0)],
                ['rgba(205, 76, 76, 0.76)']
            )
        },
        latencyChart() {
            return this.buildBarChart(
                [this.local('Avg Latency')],
                [this.modelPoolModels.map((model) => model.stats?.avg_latency_ms || 0)],
                ['rgba(48, 156, 117, 0.76)']
            )
        },
        tokensChart() {
            return {
                labels: this.modelChartLabels,
                datasets: [
                    {
                        data: this.modelPoolModels.map(
                            (model) => model.stats?.usage?.total_tokens || 0
                        ),
                        backgroundColor: [
                            'rgba(45, 125, 210, 0.78)',
                            'rgba(48, 156, 117, 0.78)',
                            'rgba(229, 154, 64, 0.78)',
                            'rgba(132, 92, 196, 0.78)',
                            'rgba(205, 76, 76, 0.78)'
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
    created() {
        this.ensureModelPoolConfig()
    },
    mounted() {
        this.getConfigs().then(() => {
            this.ensureModelPoolConfig()
            this.loadModelPoolStatus(false).catch(() => {})
        })
    },
    methods: {
        ...mapActions(useAppConfig, ['setLanguage']),
        ...mapActions(useLoopAI, ['getConfigs']),
        handleSelectDataset(item) {
            this.show.dataset = true
            this.currentSelectItem = item
        },
        handleDatasetConfirm(event) {
            if (this.currentSelectItem === null) return
            this.currentSelectItem.value = event.path
            this.show.dataset = false
        },
        setSystemValue(key, value) {
            if (!this.config.system) this.config.system = {}
            if (!this.config.system[key]) {
                this.config.system[key] = this.wrappedValue(value)
                return
            }
            this.config.system[key].value = value
        },
        wrappedValue(value, type = null) {
            return {
                value,
                default_value: JSON.parse(JSON.stringify(value)),
                type:
                    type ||
                    (Array.isArray(value)
                        ? 'list'
                        : typeof value === 'object' && value !== null
                          ? 'dict'
                          : typeof value)
            }
        },
        systemValue(key, fallback = '') {
            const item = this.config.system?.[key]
            if (!item) return fallback
            return item.value === undefined || item.value === null ? fallback : item.value
        },
        buildLegacyModelPoolConfig() {
            const apiPort = this.systemValue('api_port', 8855)
            const pool = []
            const starterModel =
                this.systemValue('starter_model_path', '') ||
                this.systemValue('starter_model_name', '')
            const starterBaseUrl = this.systemValue('starter_base_url', '')
            if (starterModel || starterBaseUrl) {
                pool.push({
                    tier: 'medium',
                    name: 'starter',
                    api_key: this.systemValue('starter_api_key', ''),
                    base_url: starterBaseUrl,
                    model_name: starterModel,
                    maxworker: this.systemValue('starter_maxworker', 1),
                    wire_api: this.systemValue('starter_wire_api', 'chat'),
                    response_format: '',
                    enabled: true
                })
            }
            const codexModel = this.systemValue('codex_model', '')
            const codexBaseUrl = this.systemValue('codex_base_url', '')
            if (codexModel || codexBaseUrl) {
                pool.push({
                    tier: pool.length ? 'high' : 'medium',
                    name: 'codex',
                    api_key: this.systemValue('codex_api_key', ''),
                    base_url: codexBaseUrl,
                    model_name: codexModel,
                    maxworker: this.systemValue('codex_maxworker', 1),
                    wire_api: this.systemValue('codex_wire_api', 'responses'),
                    response_format: '',
                    enabled: true
                })
            }
            return {
                proxy_base_url: `http://127.0.0.1:${apiPort || 8855}/responseProxy/v1`,
                proxy_api_key: 'loopai-local-proxy',
                default_tier: pool.some((model) => model.tier === 'medium') ? 'medium' : 'high',
                pool
            }
        },
        ensureModelPoolConfig() {
            if (!this.config.system) this.config.system = {}
            const current = this.config.system.model?.value
            if (!current || typeof current !== 'object' || Array.isArray(current)) {
                this.config.system.model = this.wrappedValue(
                    this.buildLegacyModelPoolConfig(),
                    'dict'
                )
            } else {
                if (!Array.isArray(current.pool)) {
                    current.pool = Array.isArray(current.models) ? current.models : []
                }
                if (!current.pool.length) {
                    current.pool = this.buildLegacyModelPoolConfig().pool
                }
                if (!current.proxy_base_url) {
                    current.proxy_base_url = this.buildLegacyModelPoolConfig().proxy_base_url
                }
                if (!current.proxy_api_key) current.proxy_api_key = 'loopai-local-proxy'
                if (!current.default_tier) current.default_tier = 'medium'
            }
            if (!this.editableModelPool.length) this.addModelPoolEntry()
            const codexModel = this.systemValue('codex_model', '')
            if (!codexModel && this.editableModelPool[0]) {
                this.setSystemValue(
                    'codex_model',
                    this.editableModelPool[0].name || this.editableModelPool[0].model_name
                )
            }
        },
        addModelPoolEntry() {
            const index = this.editableModelPool.length + 1
            this.editableModelPool.push({
                tier: 'medium',
                name: `model-${index}`,
                api_key: '',
                base_url: '',
                model_name: '',
                maxworker: 1,
                wire_api: 'auto',
                response_format: '',
                enabled: true
            })
        },
        removeModelPoolEntry(index) {
            this.editableModelPool.splice(index, 1)
            if (!this.editableModelPool.length) this.addModelPoolEntry()
        },
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
        async handleProbe() {
            if (this.modelPoolProbeLoading) return
            this.modelPoolProbeLoading = true
            this.modelPoolProbeMessage = this.local('Probing models...')
            this.show.modelPool = true
            try {
                await this.persistConfig({ silent: true })
                await this.loadModelPoolStatus(true)
                const hasHealthyModel = this.modelPoolModels.some(
                    (model) => this.healthClass(model) === 'healthy'
                )
                const hasProbedModel = this.modelPoolModels.some((model) =>
                    this.hasProbeResult(model)
                )
                this.modelPoolProbeMessage = hasHealthyModel
                    ? this.local('Probe finished.')
                    : hasProbedModel
                      ? this.local('Probe finished with unavailable models.')
                      : this.local('Probe finished.')
                this.$barWarning(this.modelPoolProbeMessage, {
                    status: hasHealthyModel ? 'correct' : 'warning'
                })
            } catch (error) {
                this.modelPoolProbeMessage = this.local('Probe failed.')
                this.$barWarning(this.local('Probe failed.'), { status: 'error' })
            } finally {
                this.modelPoolProbeLoading = false
            }
        },
        async loadModelPoolStatus(force = false) {
            const base = getBaseURL()
            const url = force
                ? `${base}/responseProxy/pool/probe?force=true`
                : `${base}/responseProxy/pool/status`
            const resp = await fetch(url, { method: force ? 'POST' : 'GET' })
            if (!resp.ok) throw new Error(`model pool status failed: ${resp.status}`)
            this.modelPoolStatus = await resp.json()
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
            if (selected) return `${this.local('Online')} · ${selected}`
            if (model.probe?.chat?.ok) return `${this.local('Online')} · chat`
            if (model.probe?.responses?.ok) return `${this.local('Online')} · responses`
            if (this.hasProbeFailure(model)) return this.local('Unavailable')
            return this.local('Not Probed')
        },
        healthClass(model) {
            const selected = model.probe?.selected_wire_api
            if (selected || model.probe?.chat?.ok || model.probe?.responses?.ok) return 'healthy'
            if (model.enabled === false) return 'disabled'
            if (this.hasProbeFailure(model) || this.hasProbeResult(model)) return 'unhealthy'
            return 'unknown'
        },
        formatCompact(value) {
            const number = Number(value || 0)
            if (number >= 1000000) return `${(number / 1000000).toFixed(1)}m`
            if (number >= 1000) return `${(number / 1000).toFixed(1)}k`
            return String(number)
        },
        async updateConfig() {
            if (!this.lock.update) return
            this.lock.update = false
            const res = await this.persistConfig()
            if (res?.code === 200) {
                this.$barWarning(this.local('Update Config Success.'), {
                    status: 'correct'
                })
                this.refreshLanguage()
                this.loadModelPoolStatus(false).catch(() => {})
            }
            this.lock.update = true
        },
        async persistConfig({ silent = false } = {}) {
            this.ensureModelPoolConfig()
            const res = await this.$api.config.updateConfig({
                id: this.configId,
                config: JSON.stringify(this.config)
            })
            if (!silent && res.code !== 200) {
                this.$barWarning(res.message || this.local('Update Config Failed.'), {
                    status: 'error'
                })
            }
            return res
        },
        async refreshLanguage() {
            await this.getConfigs()
                .then((res) => {
                    let language = 'en'
                    try {
                        language = res.data.states.default.language.value
                    } catch (error) {}
                    if (!language) language = 'en'
                    this.setLanguage(language)
                })
                .catch((error) => {
                    console.log(error)
                })
        },
        valueBuilder(item) {
            let type = item.type
            return this.formatValues[type](item.value)
        },
        reset() {
            for (let key in this.config.system) {
                if (this.config.system[key]) {
                    for (let param_key in this.config.system[key]) {
                        this.config.system[key][param_key].value =
                            this.config.system[key][param_key].default_value === null
                                ? ''
                                : this.config.system[key][param_key].default_value
                    }
                }
            }
            for (let key in this.config.states) {
                if (this.config.states[key]) {
                    for (let param_key in this.config.states[key]) {
                        this.config.states[key][param_key].value =
                            this.config.states[key][param_key].default_value === null
                                ? ''
                                : this.config.states[key][param_key].default_value
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
    background-color: rgba(243, 243, 243, 1);
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

            .lp-serving-title {
                margin: 10px 0px;
                font-size: 18px;
                font-weight: 500;
                color: rgba(50, 49, 47, 1);
            }

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
