<template>
    <div class="lp-serving-container">
        <div class="major-container">
            <header class="lp-bar">
                <span class="lp-bar__title">{{ local('Global Config') }}</span>
                <span class="lp-config__scope">{{ local('applies to every task') }}</span>
                <div class="lp-bar__spacer"></div>
                <template v-if="dirty">
                    <span class="lp-config__dirty">{{ local('unsaved changes') }}</span>
                    <button type="button" class="lp-btn lp-btn--ghost" @click="discard">
                        {{ local('Discard') }}
                    </button>
                </template>
                <button
                    type="button"
                    class="lp-btn lp-btn--ghost"
                    :disabled="!lock.update"
                    :title="local('Restore every value to its default')"
                    @click="reset"
                >
                    {{ local('Defaults') }}
                </button>
                <button
                    type="button"
                    class="lp-btn lp-btn--primary"
                    :disabled="!lock.update || !dirty"
                    @click="updateConfig"
                >
                    {{ local('Save') }}
                </button>
            </header>
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
    </div>
</template>

<script>
import { mapState, mapActions } from 'pinia'
import { getBaseURL } from '@/axios/config'
import { useAppConfig } from '@/stores/appConfig'
import { useTheme } from '@/stores/theme'
import { useLoopAI } from '@/stores/loopAI'

import ModelPoolPanel from '@/components/manage/config/modelPool/index.vue'
import valueInput from '@/components/manage/config/valueInput/index.vue'
import resourcePanel from '@/components/manage/mainFlow/panels/resourcePanel/index.vue'

export default {
    components: {
        ModelPoolPanel,
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
            baseline: '',
            lock: {
                update: true
            },
            show: {
                dataset: false
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
                    default_model: '',
                    codex_model: '',
                    analyzer_model: '',
                    looper_model: '',
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
        /* The save bar only shows up once there is something to save. */
        dirty() {
            if (!this.baseline) return false
            return JSON.stringify(this.config) !== this.baseline
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
                const defaultModel = this.modelPoolConfig.default_model
                const matched =
                    this.modelSelectOptions.find((option) => option.model?.name === defaultModel) ||
                    this.modelSelectOptions.find(
                        (option) => option.model?.model_name === defaultModel
                    ) ||
                    this.modelSelectOptions.find((option) => option.model?.tier === defaultTier) ||
                    this.modelSelectOptions[0]
                return matched || null
            },
            set(option) {
                if (!option || option.placeholder) return
                const model = option.model || {}
                if (this.config.system?.model?.value) {
                    this.config.system.model.value.default_model = model.name || model.model_name || option.key
                    if (model.tier) this.config.system.model.value.default_tier = model.tier
                }
            }
        },
        codexModelOption: {
            get() {
                const codexModel = this.modelPoolConfig.codex_model
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
                if (this.config.system?.model?.value) {
                    this.config.system.model.value.codex_model = model.name || model.model_name || option.key
                }
            }
        },
        analyzerModelOption: {
            get() {
                const analyzerModel = this.modelPoolConfig.analyzer_model || this.modelPoolConfig.default_model
                const matched =
                    this.modelSelectOptions.find((option) => option.model?.name === analyzerModel) ||
                    this.modelSelectOptions.find(
                        (option) => option.model?.model_name === analyzerModel
                    ) ||
                    this.selectedModelOption ||
                    this.modelSelectOptions[0]
                return matched || null
            },
            set(option) {
                if (!option || option.placeholder) return
                const model = option.model || {}
                if (this.config.system?.model?.value) {
                    this.config.system.model.value.analyzer_model =
                        model.name || model.model_name || option.key
                }
            }
        },
        looperModelOption: {
            get() {
                const looperModel = this.modelPoolConfig.looper_model || this.modelPoolConfig.default_model
                const matched =
                    this.modelSelectOptions.find((option) => option.model?.name === looperModel) ||
                    this.modelSelectOptions.find(
                        (option) => option.model?.model_name === looperModel
                    ) ||
                    this.selectedModelOption ||
                    this.modelSelectOptions[0]
                return matched || null
            },
            set(option) {
                if (!option || option.placeholder) return
                const model = option.model || {}
                if (this.config.system?.model?.value) {
                    this.config.system.model.value.looper_model =
                        model.name || model.model_name || option.key
                }
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
                ['rgba(87, 99, 206, 0.76)']
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
                            'rgba(87, 99, 206, 0.78)',
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
            this.snapshot()
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
        resolvePreferredModelNames(pool = this.editableModelPool) {
            const items = Array.isArray(pool) ? pool : []
            const firstEnabled =
                items.find((model) => model?.enabled !== false && (model.name || model.model_name)) ||
                items.find((model) => model.name || model.model_name) ||
                null
            const codexPreferred =
                items.find((model) => model?.name === 'codex') ||
                items.find((model) => model?.model_name === 'codex') ||
                firstEnabled
            const defaultName = firstEnabled?.name || firstEnabled?.model_name || ''
            const codexName = codexPreferred?.name || codexPreferred?.model_name || defaultName
            return {
                defaultModel: defaultName,
                codexModel: codexName,
                analyzerModel: defaultName,
                looperModel: defaultName
            }
        },
        buildLegacyModelPoolConfig() {
            return {
                proxy_base_url: 'http://127.0.0.1:8855/responseProxy/v1',
                proxy_api_key: 'loopai-local-proxy',
                default_model: '',
                codex_model: '',
                analyzer_model: '',
                looper_model: '',
                default_tier: 'medium',
                pool: []
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
            const normalized = this.config.system.model?.value
            const preferred = this.resolvePreferredModelNames(normalized?.pool)
            if (normalized) {
                if (!normalized.default_model) normalized.default_model = preferred.defaultModel
                if (!normalized.codex_model) normalized.codex_model = preferred.codexModel
                if (!normalized.analyzer_model) {
                    normalized.analyzer_model = normalized.default_model || preferred.analyzerModel
                }
                if (!normalized.looper_model) {
                    normalized.looper_model = normalized.default_model || preferred.looperModel
                }
            }
            if (!this.editableModelPool.length) this.addModelPoolEntry()
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
                this.snapshot()
                this.$barWarning(this.local('Update Config Success.'), {
                    status: 'correct'
                })
                this.refreshLanguage()
                this.loadModelPoolStatus(false).catch(() => {})
            }
            this.lock.update = true
        },
        snapshot() {
            this.baseline = JSON.stringify(this.config)
        },
        discard() {
            this.getConfigs().then(() => this.snapshot())
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
    background: var(--lp-bg);
    display: flex;
    justify-content: center;

    .lp-config__scope,
    .lp-config__dirty {
        font-family: var(--lp-mono);
        font-size: var(--lp-t-sm);
        color: var(--lp-text-mute);
        white-space: nowrap;
    }

    .lp-config__dirty {
        color: var(--lp-run);
    }

    .major-container {
        position: relative;
        width: 100%;
        height: 100%;
        display: flex;
        flex-direction: column;

        .content-block {
            position: relative;
            width: 100%;
            max-width: 900px;
            height: 100%;
            margin: 0 auto;
            gap: 16px;
            padding: 24px 28px 48px 28px;
            display: flex;
            flex-direction: column;
            overflow: auto;

        .lp-serving-title {
                margin: 12px 0px 2px 0px;
                font-family: var(--lp-mono);
                font-size: var(--lp-t-sm);
                letter-spacing: 0.09em;
                text-transform: uppercase;
                color: var(--lp-text-mute);
            }

            .serving-item {
                flex-shrink: 0;

                .collapse-item-content {
                    position: relative;
                    height: auto;
                    transition: all 0.3s;
                }

                .serving-item-title {
                    margin: 4px 0px;
                    font-size: var(--lp-t-md);
                    font-weight: 600;
                    color: var(--lp-text);
                    user-select: none;
                }

                .serving-item-light-title {
                    margin: 4px 0px;
                    font-family: var(--lp-mono);
                    font-size: var(--lp-t-cap);
                    color: var(--lp-text-dim);
                    user-select: none;
                }

                .serving-item-info {
                    margin: 4px 0px;
                    font-size: 11.5px;
                    color: var(--lp-text-mute);
                    user-select: none;
                }

                .serving-item-bold-info {
                    margin: 4px 0px;
                    font-size: var(--lp-t-body);
                    font-weight: 600;
                    color: var(--lp-text);
                    user-select: none;
                }

                .serving-item-p-block {
                    position: relative;
                    width: 100%;
                    height: auto;
                    padding: 10px 0px;
                    line-height: 1.7;
                    display: flex;
                    flex-direction: column;
                }

                .serving-item-row {
                    position: relative;
                    width: 100%;
                    padding: 6px 14px;
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
                    border-top: var(--lp-line) solid thin;
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
