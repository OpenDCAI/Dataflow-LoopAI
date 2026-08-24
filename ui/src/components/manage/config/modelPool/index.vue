<template>
    <div class="model-pool-panel">
        <div class="model-pool-head">
            <div>
                <p class="model-pool-title">{{ local('Model Pool') }}</p>
                <p class="model-pool-sub">
                    {{ modelPoolStatus.proxy_base_url || modelPoolProxyBaseUrl || '-' }}
                </p>
            </div>
            <div class="model-pool-overview-pill" :class="modelPoolOverview.statusClass">
                <span></span>
                <p>{{ modelPoolOverview.statusText }}</p>
            </div>
            <div class="model-pool-actions">
                <fv-button
                    icon="Refresh"
                    border-radius="6"
                    :disabled="probeLoading"
                    style="width: 86px"
                    @click="$emit('probe')"
                >
                    {{ probeLoading ? local('Probing') : local('Probe') }}
                </fv-button>
                <fv-button
                    icon="OpenPane"
                    border-radius="6"
                    style="width: 86px"
                    @click="showDetailPanel = true"
                >
                    {{ local('Details') }}
                </fv-button>
            </div>
        </div>
        <div class="model-pool-dashboard">
            <div class="model-pool-metric">
                <span>{{ local('Online') }}</span>
                <p>{{ modelPoolOverview.online }}/{{ modelPoolOverview.total }}</p>
            </div>
            <div class="model-pool-metric">
                <span>{{ local('Requests') }}</span>
                <p>{{ formatCompact(modelPoolOverview.requests) }}</p>
            </div>
            <div class="model-pool-metric">
                <span>{{ local('Tokens') }}</span>
                <p>{{ formatCompact(modelPoolOverview.tokens) }}</p>
            </div>
            <div class="model-pool-metric">
                <span>{{ local('Avg Latency') }}</span>
                <p>{{ modelPoolOverview.avgLatency || '-' }} ms</p>
            </div>
        </div>
        <div class="model-tier-strip">
            <div
                class="model-tier-card"
                v-for="tier in modelPoolTiers"
                :key="tier.name"
                :class="tier.name"
            >
                <div class="tier-card-top">
                    <span class="tier-dot"></span>
                    <p class="tier-name">{{ tier.name }}</p>
                    <span class="tier-badge">{{ tier.healthy }}/{{ tier.total }}</span>
                </div>
                <div class="tier-bar">
                    <span :style="{ width: tier.rate + '%' }"></span>
                </div>
                <div class="tier-card-foot">
                    <span>{{ formatCompact(tier.requests) }} req</span>
                    <span>{{ tier.errors }} err</span>
                </div>
            </div>
        </div>
        <div class="model-node-grid" v-if="modelNodeRows.length">
            <div
                class="model-node-card"
                v-for="node in modelNodeRows"
                :key="node.key"
                :class="[node.healthClass, node.tier]"
            >
                <div class="node-main">
                    <div class="node-status-dot"></div>
                    <div class="node-name-block">
                        <p>{{ node.name }}</p>
                        <span>{{ node.modelName }}</span>
                    </div>
                    <span class="node-tier">{{ node.tier }}</span>
                </div>
                <div class="node-line">
                    <span>{{ node.statusText }}</span>
                    <span>{{ node.api }}</span>
                    <span>{{ node.latency }}</span>
                </div>
                <div class="node-usage">
                    <span>{{ formatCompact(node.requests) }} req</span>
                    <span>{{ formatCompact(node.tokens) }} tok</span>
                    <span>{{ node.errors }} err</span>
                </div>
            </div>
        </div>
        <div class="model-setting-grid">
            <div class="model-setting-row">
                <p class="serving-item-light-title">{{ local('Default Model') }}</p>
                <fv-combobox
                    v-model="selectedModelOption"
                    :options="modelSelectOptions"
                    :choosen-slider-background="color"
                    border-radius="6"
                    style="width: 100%"
                ></fv-combobox>
            </div>
            <div class="model-setting-row">
                <p class="serving-item-light-title">{{ local('Codex Model') }}</p>
                <fv-combobox
                    v-model="codexModelOption"
                    :options="modelSelectOptions"
                    :choosen-slider-background="color"
                    border-radius="6"
                    style="width: 100%"
                ></fv-combobox>
            </div>
            <div class="model-setting-row">
                <p class="serving-item-light-title">{{ local('Analyzer Model') }}</p>
                <fv-combobox
                    v-model="analyzerModelOption"
                    :options="modelSelectOptions"
                    :choosen-slider-background="color"
                    border-radius="6"
                    style="width: 100%"
                ></fv-combobox>
            </div>
            <div class="model-setting-row">
                <p class="serving-item-light-title">{{ local('Looper Model') }}</p>
                <fv-combobox
                    v-model="looperModelOption"
                    :options="modelSelectOptions"
                    :choosen-slider-background="color"
                    border-radius="6"
                    style="width: 100%"
                ></fv-combobox>
            </div>
            <div class="model-setting-row">
                <p class="serving-item-light-title">{{ local('Default Tier') }}</p>
                <fv-combobox
                    v-model="defaultTierModel"
                    :placeholder="local('Select Default Tier')"
                    :options="tierOptions"
                    :choosen-slider-background="color"
                    border-radius="6"
                    style="width: 100%"
                ></fv-combobox>
            </div>
            <div class="model-setting-row">
                <p class="serving-item-light-title">{{ local('Proxy URL') }}</p>
                <fv-text-box
                    v-model="modelPoolConfig.proxy_base_url"
                    border-radius="3"
                    :border-width="2"
                    :reveal-border="true"
                    :border-color="'rgba(120, 120, 120, 0.1)'"
                    :focus-border-color="color"
                    :is-box-shadow="true"
                    underline
                    style="width: 100%"
                ></fv-text-box>
            </div>
        </div>
        <div class="model-register-head">
            <p class="model-register-title">{{ local('Registered Models') }}</p>
            <fv-button icon="Add" border-radius="6" style="width: 104px" @click="addModelPoolEntry">
                {{ local('Add Model') }}
            </fv-button>
        </div>
        <div class="model-register-list">
            <model-pool-item
                v-for="(model, index) in editableModelPool"
                :key="index"
                :index="index"
                :model="model"
                :tier-options="tierOptions"
                :wire-api-options="wireApiOptions"
                :response-format-options="responseFormatOptions"
                :local="local"
                @remove="removeModelPoolEntry"
            ></model-pool-item>
        </div>
        <div class="service-config-grid">
            <section class="service-config-block">
                <div class="service-config-head">
                    <i class="ms-Icon ms-Icon--Database"></i>
                    <p>{{ local('Embedding Model') }}</p>
                </div>
                <div class="service-config-fields">
                    <div class="field-item">
                        <span>{{ local('Provider') }}</span>
                        <fv-text-box
                            v-model="embeddingConfig.provider"
                            border-radius="3"
                            :border-width="2"
                            :reveal-border="true"
                            :border-color="'rgba(120, 120, 120, 0.1)'"
                            :focus-border-color="color"
                            :is-box-shadow="true"
                            underline
                            style="width: 100%"
                        ></fv-text-box>
                    </div>
                    <div class="field-item wide">
                        <span>{{ local('Base URL') }}</span>
                        <fv-text-box
                            v-model="embeddingConfig.base_url"
                            border-radius="3"
                            :border-width="2"
                            :reveal-border="true"
                            :border-color="'rgba(120, 120, 120, 0.1)'"
                            :focus-border-color="color"
                            :is-box-shadow="true"
                            underline
                            style="width: 100%"
                        ></fv-text-box>
                    </div>
                    <div class="field-item wide">
                        <span>{{ local('API Key') }}</span>
                        <fv-text-box
                            v-model="embeddingConfig.api_key"
                            border-radius="3"
                            :border-width="2"
                            :reveal-border="true"
                            :border-color="'rgba(120, 120, 120, 0.1)'"
                            :focus-border-color="color"
                            :is-box-shadow="true"
                            underline
                            style="width: 100%"
                        ></fv-text-box>
                    </div>
                    <div class="field-item">
                        <span>{{ local('Model') }}</span>
                        <fv-text-box
                            v-model="embeddingConfig.model"
                            border-radius="3"
                            :border-width="2"
                            :reveal-border="true"
                            :border-color="'rgba(120, 120, 120, 0.1)'"
                            :focus-border-color="color"
                            :is-box-shadow="true"
                            underline
                            style="width: 100%"
                        ></fv-text-box>
                    </div>
                    <div class="field-item">
                        <span>{{ local('Backend') }}</span>
                        <fv-text-box
                            v-model="embeddingConfig.backend"
                            border-radius="3"
                            :border-width="2"
                            :reveal-border="true"
                            :border-color="'rgba(120, 120, 120, 0.1)'"
                            :focus-border-color="color"
                            :is-box-shadow="true"
                            underline
                            style="width: 100%"
                        ></fv-text-box>
                    </div>
                    <div class="field-item">
                        <span>{{ local('Text Field') }}</span>
                        <fv-text-box
                            v-model="embeddingConfig.text_field"
                            border-radius="3"
                            :border-width="2"
                            :reveal-border="true"
                            :border-color="'rgba(120, 120, 120, 0.1)'"
                            :focus-border-color="color"
                            :is-box-shadow="true"
                            underline
                            style="width: 100%"
                        ></fv-text-box>
                    </div>
                </div>
            </section>
            <section class="service-config-block">
                <div class="service-config-head">
                    <i class="ms-Icon ms-Icon--DocumentSearch"></i>
                    <p>{{ local('MinerU-HTML Document Parsing') }}</p>
                </div>
                <div class="service-config-fields">
                    <div class="field-item wide">
                        <span>{{ local('Service URL') }}</span>
                        <fv-text-box
                            v-model="mineruConfig.url"
                            border-radius="3"
                            :border-width="2"
                            :reveal-border="true"
                            :border-color="'rgba(120, 120, 120, 0.1)'"
                            :focus-border-color="color"
                            :is-box-shadow="true"
                            underline
                            style="width: 100%"
                        ></fv-text-box>
                    </div>
                    <div class="field-item wide">
                        <span>{{ local('Python Env Path') }}</span>
                        <fv-text-box
                            v-model="mineruConfig.python"
                            border-radius="3"
                            :border-width="2"
                            :reveal-border="true"
                            :border-color="'rgba(120, 120, 120, 0.1)'"
                            :focus-border-color="color"
                            :is-box-shadow="true"
                            underline
                            style="width: 100%"
                        ></fv-text-box>
                    </div>
                    <div class="field-item wide">
                        <span>{{ local('Model Path') }}</span>
                        <fv-text-box
                            v-model="mineruConfig.model"
                            border-radius="3"
                            :border-width="2"
                            :reveal-border="true"
                            :border-color="'rgba(120, 120, 120, 0.1)'"
                            :focus-border-color="color"
                            :is-box-shadow="true"
                            underline
                            style="width: 100%"
                        ></fv-text-box>
                    </div>
                    <div class="field-item">
                        <span>{{ local('GPU') }}</span>
                        <fv-text-box
                            v-model="mineruConfig.gpu"
                            border-radius="3"
                            :border-width="2"
                            :reveal-border="true"
                            :border-color="'rgba(120, 120, 120, 0.1)'"
                            :focus-border-color="color"
                            :is-box-shadow="true"
                            underline
                            style="width: 100%"
                        ></fv-text-box>
                    </div>
                    <div class="field-item">
                        <span>{{ local('Transport') }}</span>
                        <fv-combobox
                            v-model="mineruTransportModel"
                            :placeholder="local('Select Transport')"
                            :options="mineruTransportOptions"
                            :choosen-slider-background="color"
                            border-radius="6"
                            style="width: 100%"
                        ></fv-combobox>
                    </div>
                    <div class="field-item">
                        <span>{{ local('Backend') }}</span>
                        <fv-combobox
                            v-model="mineruBackendModel"
                            :placeholder="local('Select Backend')"
                            :options="mineruBackendOptions"
                            :choosen-slider-background="color"
                            border-radius="6"
                            style="width: 100%"
                        ></fv-combobox>
                    </div>
                </div>
            </section>
        </div>
        <p v-if="probeMessage" class="model-probe-message">{{ probeMessage }}</p>
        <model-pool-detail-panel
            v-model="showDetailPanel"
            :config="config"
            :status="modelPoolStatus"
        ></model-pool-detail-panel>
    </div>
</template>

<script>
import { mapState } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'
import { useTheme } from '@/stores/theme.js'
import ModelPoolItem from './modelPoolItem.vue'
import ModelPoolDetailPanel from './detailPanel.vue'

export default {
    name: 'ModelPool',
    components: {
        ModelPoolItem,
        ModelPoolDetailPanel
    },
    props: {
        config: { type: Object, required: true },
        status: { type: Object, default: () => ({}) },
        probeLoading: { type: Boolean, default: false },
        probeMessage: { type: String, default: '' }
    },
    emits: ['probe'],
    data() {
        return {
            showDetailPanel: false,
            tierOptions: [
                { key: 'high', text: 'high' },
                { key: 'medium', text: 'medium' },
                { key: 'low', text: 'low' }
            ],
            wireApiOptions: [
                { key: 'auto', text: 'auto' },
                { key: 'responses', text: 'responses' },
                { key: 'chat', text: 'chat' }
            ],
            responseFormatOptions: [
                { key: '', text: 'auto' },
                { key: 'responses', text: 'responses' },
                { key: 'chat', text: 'chat/completions' }
            ],
            mineruTransportOptions: [
                { key: 'http', text: 'http' },
                { key: 'worker', text: 'worker' },
                { key: 'auto', text: 'auto' }
            ],
            mineruBackendOptions: [
                { key: 'vllm', text: 'vllm' },
                { key: 'transformers', text: 'transformers' }
            ]
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        ...mapState(useTheme, ['theme', 'color']),
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
        defaultTierModel: {
            get() {
                let item = this.tierOptions.find(
                    (tier) => tier.key === this.modelPoolConfig.default_tier
                )
                return item || {}
            },
            set(value) {
                this.modelPoolConfig.default_tier = value.key || ''
            }
        },
        editableModelPool() {
            if (!Array.isArray(this.modelPoolConfig.pool)) return []
            return this.modelPoolConfig.pool
        },
        embeddingConfig() {
            this.ensureServiceConfig()
            return this.modelPoolValue?.embedding || {}
        },
        mineruConfig() {
            this.ensureServiceConfig()
            return this.modelPoolValue?.mineru || {}
        },
        mineruTransportModel: {
            get() {
                const value = this.mineruConfig.transport
                let item = this.mineruTransportOptions.find((opt) => opt.key === value)
                return item || {}
            },
            set(value) {
                if (value?.key) this.mineruConfig.transport = value.key
            }
        },
        mineruBackendModel: {
            get() {
                const value = this.mineruConfig.backend
                let item = this.mineruBackendOptions.find((opt) => opt.key === value)
                return item || {}
            },
            set(value) {
                if (value?.key) this.mineruConfig.backend = value.key
            }
        },
        statusModelPool() {
            return this.status.models || []
        },
        modelPoolStatus() {
            return this.status || {}
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
                const selected = model.probe?.selected_wire_api
                return {
                    key: model.name || model.model_name || index,
                    name: model.name || model.model_name || 'model',
                    modelName: model.model_name || model.name || '-',
                    tier: model.tier || 'medium',
                    healthClass: this.healthClass(model),
                    statusText: this.healthText(model),
                    api: selected || model.wire_api || 'auto',
                    latency: model.stats?.avg_latency_ms
                        ? `${model.stats.avg_latency_ms} ms`
                        : '- ms',
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
                return (
                    this.modelSelectOptions.find((option) => option.model?.name === defaultModel) ||
                    this.modelSelectOptions.find(
                        (option) => option.model?.model_name === defaultModel
                    ) ||
                    this.modelSelectOptions.find((option) => option.model?.tier === defaultTier) ||
                    this.modelSelectOptions[0] ||
                    null
                )
            },
            set(option) {
                if (!option || option.placeholder) return
                const model = option.model || {}
                if (this.config.system?.model?.value) {
                    this.config.system.model.value.default_model =
                        model.name || model.model_name || option.key
                    if (model.tier) this.config.system.model.value.default_tier = model.tier
                }
            }
        },
        codexModelOption: {
            get() {
                const codexModel = this.modelPoolConfig.codex_model
                return (
                    this.modelSelectOptions.find((option) => option.model?.name === codexModel) ||
                    this.modelSelectOptions.find(
                        (option) => option.model?.model_name === codexModel
                    ) ||
                    this.modelSelectOptions.find((option) => option.model?.name === 'codex') ||
                    this.modelSelectOptions[0] ||
                    null
                )
            },
            set(option) {
                if (!option || option.placeholder) return
                const model = option.model || {}
                if (this.config.system?.model?.value) {
                    this.config.system.model.value.codex_model =
                        model.name || model.model_name || option.key
                }
            }
        },
        analyzerModelOption: {
            get() {
                const analyzerModel =
                    this.modelPoolConfig.analyzer_model || this.modelPoolConfig.default_model
                return (
                    this.modelSelectOptions.find(
                        (option) => option.model?.name === analyzerModel
                    ) ||
                    this.modelSelectOptions.find(
                        (option) => option.model?.model_name === analyzerModel
                    ) ||
                    this.selectedModelOption ||
                    this.modelSelectOptions[0] ||
                    null
                )
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
                const looperModel =
                    this.modelPoolConfig.looper_model || this.modelPoolConfig.default_model
                return (
                    this.modelSelectOptions.find((option) => option.model?.name === looperModel) ||
                    this.modelSelectOptions.find(
                        (option) => option.model?.model_name === looperModel
                    ) ||
                    this.selectedModelOption ||
                    this.modelSelectOptions[0] ||
                    null
                )
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
            return ['high', 'medium', 'low'].map((tier) => {
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
        }
    },
    methods: {
        resolvePreferredModelNames() {
            const pool = Array.isArray(this.editableModelPool) ? this.editableModelPool : []
            const firstEnabled =
                pool.find(
                    (model) => model?.enabled !== false && (model.name || model.model_name)
                ) ||
                pool.find((model) => model.name || model.model_name) ||
                null
            const codexPreferred =
                pool.find((model) => model?.name === 'codex') ||
                pool.find((model) => model?.model_name === 'codex') ||
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
        normalizeModelConfig(current) {
            if (!current || typeof current !== 'object' || Array.isArray(current)) return
            if (!Array.isArray(current.pool)) {
                current.pool = Array.isArray(current.models) ? current.models : []
            }
            const preferred = this.resolvePreferredModelNames()
            if (!current.proxy_base_url)
                current.proxy_base_url = 'http://127.0.0.1:8855/responseProxy/v1'
            if (!current.proxy_api_key) current.proxy_api_key = 'loopai-local-proxy'
            if (!current.default_tier) current.default_tier = 'medium'
            if (!current.default_model) current.default_model = preferred.defaultModel
            if (!current.codex_model) current.codex_model = preferred.codexModel
            if (!current.analyzer_model) {
                current.analyzer_model = current.default_model || preferred.analyzerModel
            }
            if (!current.looper_model) {
                current.looper_model = current.default_model || preferred.looperModel
            }
        },
        ensureServiceConfig() {
            const value = this.modelPoolValue
            if (!value || typeof value !== 'object' || Array.isArray(value)) return false
            if (!value.embedding || typeof value.embedding !== 'object' || Array.isArray(value.embedding)) {
                value.embedding = {
                    provider: 'openai-compatible',
                    base_url: 'http://127.0.0.1:8000/v1',
                    api_key: '',
                    model: 'BAAI/bge-small-zh-v1.5',
                    backend: 'local-jsonl',
                    text_field: 'text'
                }
            }
            if (!value.mineru || typeof value.mineru !== 'object' || Array.isArray(value.mineru)) {
                value.mineru = {
                    url: 'http://127.0.0.1:7986',
                    python: '',
                    model: '',
                    gpu: '0',
                    transport: 'http',
                    backend: 'vllm'
                }
            }
            return true
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
        }
    }
}
</script>

<style lang="scss" scoped>
.model-pool-panel {
    width: 100%;
    padding: 14px;
    box-sizing: border-box;
    border: 1px solid rgba(210, 213, 222, 0.9);
    border-radius: 8px;
    background: rgba(255, 255, 255, 0.86);
    display: flex;
    flex-direction: column;
    gap: 12px;
    flex-shrink: 0;
}
.model-pool-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
}
.model-pool-title {
    font-size: 16px;
    font-weight: 600;
    color: rgba(36, 36, 36, 1);
}
.model-pool-sub {
    margin-top: 4px;
    font-size: 12px;
    color: rgba(98, 98, 98, 1);
    word-break: break-all;
}
.model-pool-actions {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 6px;
    flex-shrink: 0;
}
.model-pool-overview-pill {
    height: 30px;
    padding: 0 10px;
    border: 1px solid rgba(218, 221, 230, 1);
    border-radius: 6px;
    background: rgba(248, 249, 251, 1);
    display: flex;
    align-items: center;
    gap: 7px;
    flex-shrink: 0;
}
.model-pool-overview-pill span {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: rgba(142, 148, 164, 1);
}
.model-pool-overview-pill p {
    font-size: 12px;
    font-weight: 600;
    color: rgba(58, 64, 78, 1);
    white-space: nowrap;
}
.model-pool-overview-pill.healthy span {
    background: rgba(38, 166, 112, 1);
}
.model-pool-overview-pill.warning span {
    background: rgba(229, 154, 64, 1);
}
.model-pool-overview-pill.unhealthy span {
    background: rgba(205, 76, 76, 1);
}
.model-pool-dashboard {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 10px;
}
.model-pool-metric {
    min-width: 0;
    padding: 10px 12px;
    border: 1px solid rgba(224, 226, 232, 1);
    border-radius: 8px;
    background: rgba(250, 251, 253, 1);
}
.model-pool-metric span {
    font-size: 11px;
    font-weight: 600;
    color: rgba(102, 108, 124, 1);
    text-transform: uppercase;
}
.model-pool-metric p {
    margin-top: 6px;
    font-size: 20px;
    font-weight: 700;
    color: rgba(31, 38, 55, 1);
    white-space: nowrap;
}
.model-tier-strip {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 10px;
}
.model-tier-card {
    min-width: 0;
    padding: 10px;
    border: 1px solid rgba(224, 226, 232, 1);
    border-radius: 8px;
    background: rgba(255, 255, 255, 0.96);
}
.model-tier-card.high {
    border-top: 3px solid rgba(38, 166, 112, 1);
}
.model-tier-card.medium {
    border-top: 3px solid rgba(87, 99, 206, 1);
}
.model-tier-card.low {
    border-top: 3px solid rgba(229, 154, 64, 1);
}
.tier-card-top {
    display: flex;
    align-items: center;
    gap: 7px;
}
.tier-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: rgba(87, 99, 206, 1);
    flex-shrink: 0;
}
.model-tier-card.high .tier-dot {
    background: rgba(38, 166, 112, 1);
}
.model-tier-card.low .tier-dot {
    background: rgba(229, 154, 64, 1);
}
.tier-name {
    flex: 1;
    min-width: 0;
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    color: rgba(45, 51, 68, 1);
}
.tier-badge {
    padding: 2px 7px;
    border-radius: 999px;
    background: rgba(242, 244, 248, 1);
    font-size: 11px;
    font-weight: 700;
    color: rgba(74, 80, 96, 1);
}
.tier-bar {
    width: 100%;
    height: 5px;
    margin: 10px 0 8px;
    border-radius: 4px;
    background: rgba(225, 229, 235, 1);
    overflow: hidden;
}
.tier-bar span {
    display: block;
    height: 100%;
    background: rgba(38, 166, 112, 1);
}
.tier-card-foot {
    display: flex;
    justify-content: space-between;
    gap: 8px;
}
.tier-card-foot span {
    min-width: 0;
    font-size: 12px;
    color: rgba(101, 107, 122, 1);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.model-node-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 10px;
}
.model-node-card {
    min-width: 0;
    padding: 10px;
    border: 1px solid rgba(224, 226, 232, 1);
    border-left: 4px solid rgba(142, 148, 164, 1);
    border-radius: 8px;
    background: rgba(255, 255, 255, 0.98);
    display: flex;
    flex-direction: column;
    gap: 9px;
}
.model-node-card.healthy {
    border-left-color: rgba(38, 166, 112, 1);
}
.model-node-card.unhealthy {
    border-left-color: rgba(205, 76, 76, 1);
}
.model-node-card.disabled {
    opacity: 0.66;
}
.node-main {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
}
.node-status-dot {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: rgba(142, 148, 164, 1);
    flex-shrink: 0;
}
.model-node-card.healthy .node-status-dot {
    background: rgba(38, 166, 112, 1);
    box-shadow: 0 0 0 3px rgba(38, 166, 112, 0.14);
}
.model-node-card.unhealthy .node-status-dot {
    background: rgba(205, 76, 76, 1);
    box-shadow: 0 0 0 3px rgba(205, 76, 76, 0.12);
}
.node-name-block {
    flex: 1;
    min-width: 0;
}
.node-name-block p {
    font-size: 13px;
    font-weight: 700;
    color: rgba(35, 40, 55, 1);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.node-name-block span {
    display: block;
    margin-top: 2px;
    font-size: 11px;
    color: rgba(103, 108, 123, 1);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.node-tier {
    padding: 2px 7px;
    border-radius: 999px;
    background: rgba(242, 244, 248, 1);
    font-size: 10px;
    font-weight: 700;
    color: rgba(75, 82, 98, 1);
    text-transform: uppercase;
    flex-shrink: 0;
}
.node-line,
.node-usage {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 6px;
}
.node-line span,
.node-usage span {
    min-width: 0;
    padding: 5px 6px;
    border-radius: 6px;
    background: rgba(245, 247, 250, 1);
    font-size: 11px;
    color: rgba(66, 72, 88, 1);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    text-align: center;
}
.model-setting-grid {
    width: 100%;
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
}
.model-setting-row {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 5px;
}
.serving-item-light-title {
    margin: 5px 0;
    font-size: 12px;
    color: rgba(95, 95, 95, 1);
    user-select: none;
}
.model-register-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
}
.model-register-title {
    font-size: 14px;
    font-weight: 600;
    color: rgba(44, 48, 60, 1);
}
.model-register-list {
    width: 100%;
    display: flex;
    flex-direction: column;
    gap: 10px;
}
.model-select-native {
    width: 100%;
    height: 32px;
    padding: 0 26px 0 8px;
    box-sizing: border-box;
    border: 1px solid rgba(210, 213, 222, 1);
    border-radius: 6px;
    background: rgba(255, 255, 255, 1);
    color: rgba(44, 48, 60, 1);
    font-size: 13px;
    outline: none;
    appearance: none;
    background-image: url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M1 1l4 4 4-4' stroke='%23616161' stroke-width='1.5' fill='none' stroke-linecap='round'/%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-position: right 9px center;
    cursor: pointer;
}
.model-select-native:hover {
    border-color: rgba(160, 166, 180, 1);
}
.model-select-native:focus {
    border-color: rgba(123, 139, 209, 1);
}
.model-probe-message {
    padding: 8px 10px;
    border-radius: 6px;
    background: rgba(238, 244, 255, 1);
    color: rgba(54, 76, 128, 1);
    font-size: 12px;
}
.service-config-grid {
    width: 100%;
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
}
.service-config-block {
    min-width: 0;
    padding: 12px;
    box-sizing: border-box;
    border: 1px solid rgba(224, 226, 232, 1);
    border-radius: 8px;
    background: rgba(249, 250, 252, 1);
    display: flex;
    flex-direction: column;
    gap: 10px;
}
.service-config-head {
    display: flex;
    align-items: center;
    gap: 7px;
}
.service-config-head i {
    font-size: 15px;
    color: rgba(64, 99, 170, 1);
}
.service-config-head p {
    font-size: 13px;
    font-weight: 600;
    color: rgba(44, 48, 60, 1);
}
.service-config-fields {
    display: grid;
    grid-template-columns: repeat(6, minmax(0, 1fr));
    gap: 10px 12px;
}
.service-config-fields .field-item {
    grid-column: span 2;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
}
.service-config-fields .field-item.wide {
    grid-column: span 3;
}
.service-config-fields .field-item span {
    font-size: 12px;
    color: rgba(95, 95, 95, 1);
}
@media (max-width: 760px) {
    .model-pool-head {
        flex-wrap: wrap;
        align-items: flex-start;
    }
    .model-pool-actions {
        width: 100%;
        justify-content: flex-start;
    }
    .model-pool-dashboard,
    .model-tier-strip,
    .model-node-grid,
    .model-setting-grid,
    .service-config-grid {
        grid-template-columns: 1fr;
    }
    .service-config-fields {
        grid-template-columns: 1fr;
    }
    .service-config-fields .field-item,
    .service-config-fields .field-item.wide {
        grid-column: span 1;
    }
}
</style>
