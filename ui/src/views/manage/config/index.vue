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
                        <div class="model-pool-panel" v-if="modelPoolAvailable">
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
                                        :disabled="modelPoolProbeLoading"
                                        style="width: 86px"
                                        @click="handleProbe"
                                    >
                                        {{ modelPoolProbeLoading ? local('Probing') : local('Probe') }}
                                    </fv-button>
                                    <fv-button
                                        icon="OpenPane"
                                        border-radius="6"
                                        style="width: 86px"
                                        @click="show.modelPool = true"
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
                                        border-radius="6"
                                        :is-box-shadow="true"
                                        style="width: 100%"
                                    ></fv-combobox>
                                </div>
                                <div class="model-setting-row">
                                    <p class="serving-item-light-title">{{ local('Codex Model') }}</p>
                                    <fv-combobox
                                        v-model="codexModelOption"
                                        :options="modelSelectOptions"
                                        border-radius="6"
                                        :is-box-shadow="true"
                                        style="width: 100%"
                                    ></fv-combobox>
                                </div>
                                <div class="model-setting-row">
                                    <p class="serving-item-light-title">{{ local('Default Tier') }}</p>
                                    <select v-model="modelPoolConfig.default_tier" class="model-select-native">
                                        <option v-for="tier in tierOptions" :key="tier" :value="tier">{{ tier }}</option>
                                    </select>
                                </div>
                                <div class="model-setting-row">
                                    <p class="serving-item-light-title">{{ local('Proxy URL') }}</p>
                                    <fv-text-box
                                        v-model="modelPoolConfig.proxy_base_url"
                                        border-radius="6"
                                        :reveal-border="true"
                                        :is-box-shadow="true"
                                        style="width: 100%"
                                    ></fv-text-box>
                                </div>
                            </div>
                            <div class="model-register-head">
                                <p class="model-register-title">{{ local('Registered Models') }}</p>
                                <fv-button
                                    icon="Add"
                                    border-radius="6"
                                    style="width: 104px"
                                    @click="addModelPoolEntry"
                                >
                                    {{ local('Add Model') }}
                                </fv-button>
                            </div>
                            <div class="model-register-list">
                                <div class="model-register-row" v-for="(model, index) in editableModelPool" :key="index">
                                    <div class="model-register-topbar">
                                        <span class="model-tier-badge" :class="model.tier">{{ model.tier }}</span>
                                        <p class="model-register-name">{{ model.name || local('Model Name') }}</p>
                                        <div class="model-register-controls">
                                            <fv-toggle-switch
                                                v-model="model.enabled"
                                                :on="local('Enabled')"
                                                :off="local('Disabled')"
                                                :width="65"
                                                :height="22"
                                            ></fv-toggle-switch>
                                            <fv-button
                                                border-radius="6"
                                                :title="local('Remove')"
                                                style="width: 32px; height: 32px"
                                                @click="removeModelPoolEntry(index)"
                                            >
                                                <i class="ms-Icon ms-Icon--Delete"></i>
                                            </fv-button>
                                        </div>
                                    </div>
                                    <div class="model-register-fields">
                                        <label>
                                            <span>{{ local('Tier') }}</span>
                                            <select v-model="model.tier" class="model-select-native">
                                                <option v-for="tier in tierOptions" :key="tier" :value="tier">
                                                    {{ tier }}
                                                </option>
                                            </select>
                                        </label>
                                        <label>
                                            <span>{{ local('Name') }}</span>
                                            <fv-text-box
                                                v-model="model.name"
                                                border-radius="6"
                                                :reveal-border="true"
                                                :is-box-shadow="true"
                                                style="width: 100%"
                                            ></fv-text-box>
                                        </label>
                                        <label>
                                            <span>{{ local('Model Name') }}</span>
                                            <fv-text-box
                                                v-model="model.model_name"
                                                border-radius="6"
                                                :reveal-border="true"
                                                :is-box-shadow="true"
                                                style="width: 100%"
                                            ></fv-text-box>
                                        </label>
                                        <label class="wide">
                                            <span>{{ local('Base URL') }}</span>
                                            <fv-text-box
                                                v-model="model.base_url"
                                                border-radius="6"
                                                :reveal-border="true"
                                                :is-box-shadow="true"
                                                style="width: 100%"
                                            ></fv-text-box>
                                        </label>
                                        <label class="wide">
                                            <span>{{ local('API Key') }}</span>
                                            <fv-text-box
                                                v-model="model.api_key"
                                                border-radius="6"
                                                :reveal-border="true"
                                                :is-box-shadow="true"
                                                style="width: 100%"
                                            ></fv-text-box>
                                        </label>
                                        <label>
                                            <span>{{ local('Max Worker') }}</span>
                                            <fv-text-box
                                                v-model="model.maxworker"
                                                border-radius="6"
                                                :reveal-border="true"
                                                :is-box-shadow="true"
                                                style="width: 100%"
                                            ></fv-text-box>
                                        </label>
                                        <label>
                                            <span>{{ local('Response API') }}</span>
                                            <select v-model="model.wire_api" class="model-select-native">
                                                <option v-for="wire in wireApiOptions" :key="wire" :value="wire">
                                                    {{ wire }}
                                                </option>
                                            </select>
                                        </label>
                                        <label>
                                            <span>{{ local('Response Format') }}</span>
                                            <select v-model="model.response_format" class="model-select-native">
                                                <option
                                                    v-for="format in responseFormatOptions"
                                                    :key="format.key"
                                                    :value="format.key"
                                                >
                                                    {{ format.text }}
                                                </option>
                                            </select>
                                        </label>
                                    </div>
                                </div>
                            </div>
                            <p v-if="modelPoolProbeMessage" class="model-probe-message">
                                {{ modelPoolProbeMessage }}
                            </p>
                        </div>
                        <hr v-if="modelPoolAvailable" />
                        <div v-if="config.system" v-for="entry in systemConfigEntries" :key="entry[0]">
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
        <base-panel
            v-model="show.modelPool"
            :title="local('Model Pool')"
            width="min(980px, 92%)"
            height="72%"
            theme="light"
            :is-footer="false"
            :teleport="true"
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
                                {{ model.tier }} · {{ model.name }}
                            </p>
                            <p class="model-detail-sub">{{ model.model_name }} · {{ model.wire_api }}</p>
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
    </div>
</template>

<script>
import { mapState, mapActions } from 'pinia'
import { getBaseURL } from '@/axios/config'
import { useAppConfig } from '@/stores/appConfig'
import { useTheme } from '@/stores/theme'
import { useLoopAI } from '@/stores/loopAI'

import valueInput from '@/components/manage/config/valueInput/index.vue'
import resourcePanel from '@/components/manage/mainFlow/panels/resourcePanel/index.vue'
import basePanel from '@/components/general/basePanel.vue'
import baseChart from '@/components/manage/obtainerLake/baseChart.vue'

export default {
    components: {
        valueInput,
        resourcePanel,
        basePanel,
        baseChart
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
            return this.modelPoolValue || {
                proxy_base_url: '',
                proxy_api_key: 'loopai-local-proxy',
                default_tier: 'medium',
                pool: []
            }
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
            const online = this.modelPoolModels.filter((model) => this.healthClass(model) === 'healthy').length
            const unhealthy = this.modelPoolModels.filter((model) => this.healthClass(model) === 'unhealthy').length
            const requests = this.modelPoolModels.reduce((sum, model) => sum + (model.stats?.requests || 0), 0)
            const errors = this.modelPoolModels.reduce((sum, model) => sum + (model.stats?.errors || 0), 0)
            const tokens = this.modelPoolModels.reduce((sum, model) => sum + (model.stats?.usage?.total_tokens || 0), 0)
            const latencyItems = this.modelPoolModels
                .map((model) => Number(model.stats?.avg_latency_ms || 0))
                .filter((value) => value > 0)
            const avgLatency = latencyItems.length
                ? Math.round(latencyItems.reduce((sum, value) => sum + value, 0) / latencyItems.length)
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
            return { total, online, unhealthy, requests, errors, tokens, avgLatency, statusClass, statusText }
        },
        modelNodeRows() {
            return this.modelPoolModels.map((model, index) => {
                const healthClass = this.healthClass(model)
                const selected = model.probe?.selected_wire_api
                const api = selected || model.wire_api || 'auto'
                const latency = model.stats?.avg_latency_ms ? `${model.stats.avg_latency_ms} ms` : '- ms'
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
            const source = this.editableModelPool.length ? this.editableModelPool : this.modelPoolModels
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
                    model: { tier: this.modelPoolConfig.default_tier || 'medium', name: '', model_name: '' },
                    placeholder: true
                })
            }
            return options
        },
        selectedModelOption: {
            get() {
                const defaultTier = this.modelPoolConfig.default_tier
                const starterModel =
                    this.config.system?.starter_model_name?.value || this.config.system?.starter_model_path?.value
                const matched =
                    this.modelSelectOptions.find((option) => option.model?.model_name === starterModel) ||
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
                this.setSystemValue('starter_model_name', model.model_name || model.name || option.key)
                this.setSystemValue('starter_model_path', model.model_name || model.name || option.key)
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
                    this.modelSelectOptions.find((option) => option.model?.model_name === codexModel) ||
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
                const requests = models.reduce((sum, model) => sum + (model.stats?.requests || 0), 0)
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
            return this.modelPoolModels.map((model) => `${model.tier || 'model'}:${model.name || model.model_name}`)
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
                        data: this.modelPoolModels.map((model) => model.stats?.usage?.total_tokens || 0),
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
                type: type || (Array.isArray(value) ? 'list' : typeof value === 'object' && value !== null ? 'dict' : typeof value)
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
            const starterModel = this.systemValue('starter_model_path', '') || this.systemValue('starter_model_name', '')
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
                this.config.system.model = this.wrappedValue(this.buildLegacyModelPoolConfig(), 'dict')
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
                this.setSystemValue('codex_model', this.editableModelPool[0].name || this.editableModelPool[0].model_name)
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
                const hasHealthyModel = this.modelPoolModels.some((model) => this.healthClass(model) === 'healthy')
                this.modelPoolProbeMessage = hasHealthyModel ? this.local('Probe finished.') : this.local('Probe failed.')
                this.$barWarning(this.modelPoolProbeMessage, { status: hasHealthyModel ? 'correct' : 'warning' })
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
                this.$barWarning(res.message || this.local('Update Config Failed.'), { status: 'error' })
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

                .model-pool-head {
                    @include HbetweenVcenter;
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
                    @include HendVcenter;
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

                    span {
                        width: 8px;
                        height: 8px;
                        border-radius: 50%;
                        background: rgba(142, 148, 164, 1);
                    }

                    p {
                        font-size: 12px;
                        font-weight: 600;
                        color: rgba(58, 64, 78, 1);
                        white-space: nowrap;
                    }

                    &.healthy span {
                        background: rgba(38, 166, 112, 1);
                    }

                    &.warning span {
                        background: rgba(229, 154, 64, 1);
                    }

                    &.unhealthy span {
                        background: rgba(205, 76, 76, 1);
                    }
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

                    span {
                        font-size: 11px;
                        font-weight: 600;
                        color: rgba(102, 108, 124, 1);
                        text-transform: uppercase;
                    }

                    p {
                        margin-top: 6px;
                        font-size: 20px;
                        font-weight: 700;
                        color: rgba(31, 38, 55, 1);
                        white-space: nowrap;
                    }
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

                    &.high {
                        border-top: 3px solid rgba(38, 166, 112, 1);
                    }

                    &.medium {
                        border-top: 3px solid rgba(45, 125, 210, 1);
                    }

                    &.low {
                        border-top: 3px solid rgba(229, 154, 64, 1);
                    }
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
                    background: rgba(45, 125, 210, 1);
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

                    span {
                        display: block;
                        height: 100%;
                        background: rgba(38, 166, 112, 1);
                    }
                }

                .tier-card-foot {
                    display: flex;
                    justify-content: space-between;
                    gap: 8px;

                    span {
                        min-width: 0;
                        font-size: 12px;
                        color: rgba(101, 107, 122, 1);
                        overflow: hidden;
                        text-overflow: ellipsis;
                        white-space: nowrap;
                    }
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

                    &.healthy {
                        border-left-color: rgba(38, 166, 112, 1);
                    }

                    &.unhealthy {
                        border-left-color: rgba(205, 76, 76, 1);
                    }

                    &.disabled {
                        opacity: 0.66;
                    }
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

                    p {
                        font-size: 13px;
                        font-weight: 700;
                        color: rgba(35, 40, 55, 1);
                        overflow: hidden;
                        text-overflow: ellipsis;
                        white-space: nowrap;
                    }

                    span {
                        display: block;
                        margin-top: 2px;
                        font-size: 11px;
                        color: rgba(103, 108, 123, 1);
                        overflow: hidden;
                        text-overflow: ellipsis;
                        white-space: nowrap;
                    }
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

                    span {
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

                .model-register-head {
                    @include HbetweenVcenter;

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

                .model-register-row {
                    width: 100%;
                    padding: 12px;
                    box-sizing: border-box;
                    border: 1px solid rgba(224, 226, 232, 1);
                    border-radius: 8px;
                    background: rgba(249, 250, 252, 1);
                    display: flex;
                    flex-direction: column;
                    gap: 12px;
                }

                .model-register-topbar {
                    display: flex;
                    align-items: center;
                    gap: 8px;
                }

                .model-register-name {
                    flex: 1;
                    min-width: 0;
                    font-size: 13px;
                    font-weight: 600;
                    color: rgba(44, 48, 60, 1);
                    overflow: hidden;
                    text-overflow: ellipsis;
                    white-space: nowrap;
                }

                .model-register-controls {
                    display: flex;
                    align-items: center;
                    gap: 10px;
                    flex-shrink: 0;
                }

                .model-tier-badge {
                    flex-shrink: 0;
                    padding: 2px 8px;
                    border-radius: 10px;
                    font-size: 11px;
                    font-weight: 600;
                    line-height: 16px;
                    text-transform: uppercase;
                    background: rgba(240, 240, 244, 1);
                    color: rgba(96, 102, 120, 1);

                    &.high {
                        background: rgba(224, 242, 233, 1);
                        color: rgba(28, 116, 86, 1);
                    }

                    &.medium {
                        background: rgba(230, 238, 252, 1);
                        color: rgba(44, 90, 168, 1);
                    }
                }

                .model-register-fields {
                    display: grid;
                    grid-template-columns: repeat(6, minmax(0, 1fr));
                    gap: 10px 12px;

                    label {
                        grid-column: span 2;
                        min-width: 0;
                        display: flex;
                        flex-direction: column;
                        gap: 4px;

                        &.wide {
                            grid-column: span 3;
                        }
                    }

                    span {
                        font-size: 12px;
                        color: rgba(95, 95, 95, 1);
                    }
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

                    &:hover {
                        border-color: rgba(160, 166, 180, 1);
                    }

                    &:focus {
                        border-color: rgba(123, 139, 209, 1);
                    }
                }

                .model-probe-message {
                    padding: 8px 10px;
                    border-radius: 6px;
                    background: rgba(238, 244, 255, 1);
                    color: rgba(54, 76, 128, 1);
                    font-size: 12px;
                }

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
    border: 1px solid rgba(218, 221, 230, 1);
    border-radius: 6px;
    background: rgba(255, 255, 255, 0.92);
    display: flex;
    flex-direction: column;
}

.model-chart-title {
    margin-bottom: 8px;
    font-size: 13px;
    font-weight: 600;
    color: rgba(44, 48, 60, 1);
}

.model-chart-card .ol-chart-shell {
    flex: 1;
    min-height: 0;
}

.model-detail-row {
    width: 100%;
    padding: 12px;
    box-sizing: border-box;
    border: 1px solid rgba(218, 221, 230, 1);
    border-radius: 6px;
    background: rgba(255, 255, 255, 0.88);
    display: grid;
    grid-template-columns: minmax(0, 1fr) 260px;
    gap: 10px;
}

.model-detail-title {
    font-size: 14px;
    font-weight: 600;
    color: rgba(35, 35, 35, 1);
    display: flex;
    align-items: center;
    gap: 7px;
}

.model-detail-dot {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: rgba(142, 148, 164, 1);
    flex-shrink: 0;

    &.healthy {
        background: rgba(38, 166, 112, 1);
        box-shadow: 0 0 0 3px rgba(38, 166, 112, 0.14);
    }

    &.unhealthy {
        background: rgba(205, 76, 76, 1);
        box-shadow: 0 0 0 3px rgba(205, 76, 76, 0.12);
    }

    &.disabled {
        background: rgba(180, 184, 194, 1);
    }
}

.model-detail-sub {
    margin-top: 4px;
    font-size: 12px;
    color: rgba(96, 96, 96, 1);
    word-break: break-all;
}

.model-detail-stats {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 6px;

    p {
        padding: 6px;
        border-radius: 6px;
        background: rgba(242, 244, 248, 1);
        font-size: 12px;
        color: rgba(54, 58, 70, 1);
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
}

.model-error {
    grid-column: 1 / -1;
    margin: 0;
    padding: 8px;
    border-radius: 6px;
    background: rgba(255, 238, 238, 1);
    color: rgba(126, 32, 32, 1);
    font-size: 12px;
    white-space: pre-wrap;
}

@media (max-width: 760px) {
    .lp-serving-container .major-container .content-block .model-pool-panel .model-pool-head {
        flex-wrap: wrap;
        align-items: flex-start;
    }

    .lp-serving-container .major-container .content-block .model-pool-panel .model-pool-actions {
        width: 100%;
        justify-content: flex-start;
    }

    .lp-serving-container .major-container .content-block .model-pool-panel .model-pool-dashboard,
    .lp-serving-container .major-container .content-block .model-pool-panel .model-tier-strip,
    .lp-serving-container .major-container .content-block .model-pool-panel .model-node-grid {
        grid-template-columns: 1fr;
    }

    .lp-serving-container .major-container .content-block .model-pool-panel .model-setting-grid,
    .lp-serving-container .major-container .content-block .model-pool-panel .model-register-fields {
        grid-template-columns: 1fr;
    }

    .lp-serving-container .major-container .content-block .model-pool-panel .model-register-fields label,
    .lp-serving-container .major-container .content-block .model-pool-panel .model-register-fields label.wide {
        grid-column: span 1;
    }

    .model-chart-grid {
        grid-template-columns: 1fr;
    }

    .model-detail-row {
        grid-template-columns: 1fr;
    }
}
</style>
