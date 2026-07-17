<template>
    <div class="model-pool-panel">
        <div class="model-pool-head">
            <div>
                <p class="model-pool-title">{{ local('Model Pool') }}</p>
                <p class="model-pool-sub">{{ modelPoolStatus.proxy_base_url || modelPoolProxyBaseUrl || '-' }}</p>
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
                    @click="$emit('show-detail')"
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
            <div class="model-tier-card" v-for="tier in modelPoolTiers" :key="tier.name" :class="tier.name">
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
            <fv-button icon="Add" border-radius="6" style="width: 104px" @click="addModelPoolEntry">
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
                            <option v-for="tier in tierOptions" :key="tier" :value="tier">{{ tier }}</option>
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
                            <option v-for="wire in wireApiOptions" :key="wire" :value="wire">{{ wire }}</option>
                        </select>
                    </label>
                    <label>
                        <span>{{ local('Response Format') }}</span>
                        <select v-model="model.response_format" class="model-select-native">
                            <option v-for="format in responseFormatOptions" :key="format.key" :value="format.key">
                                {{ format.text }}
                            </option>
                        </select>
                    </label>
                </div>
            </div>
        </div>
        <p v-if="probeMessage" class="model-probe-message">{{ probeMessage }}</p>
    </div>
</template>

<script>
import { mapState } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'

export default {
    name: 'ModelPool',
    props: {
        config: { type: Object, required: true },
        status: { type: Object, default: () => ({}) },
        probeLoading: { type: Boolean, default: false },
        probeMessage: { type: String, default: '' }
    },
    emits: ['probe', 'show-detail'],
    data() {
        return {
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
                const selected = model.probe?.selected_wire_api
                return {
                    key: model.name || model.model_name || index,
                    name: model.name || model.model_name || 'model',
                    modelName: model.model_name || model.name || '-',
                    tier: model.tier || 'medium',
                    healthClass: this.healthClass(model),
                    statusText: this.healthText(model),
                    api: selected || model.wire_api || 'auto',
                    latency: model.stats?.avg_latency_ms ? `${model.stats.avg_latency_ms} ms` : '- ms',
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
                return (
                    this.modelSelectOptions.find((option) => option.model?.model_name === starterModel) ||
                    this.modelSelectOptions.find((option) => option.model?.name === starterModel) ||
                    this.modelSelectOptions.find((option) => option.model?.tier === defaultTier) ||
                    this.modelSelectOptions[0] ||
                    null
                )
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
                return (
                    this.modelSelectOptions.find((option) => option.model?.name === codexModel) ||
                    this.modelSelectOptions.find((option) => option.model?.model_name === codexModel) ||
                    this.modelSelectOptions.find((option) => option.model?.name === 'codex') ||
                    this.modelSelectOptions[0] ||
                    null
                )
            },
            set(option) {
                if (!option || option.placeholder) return
                const model = option.model || {}
                this.setSystemValue('codex_model', model.name || model.model_name || option.key)
                this.setSystemValue('codex_wire_api', 'responses')
            }
        },
        modelPoolTiers() {
            return ['high', 'medium', 'low'].map((tier) => {
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
        }
    },
    methods: {
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
    border-top: 3px solid rgba(45, 125, 210, 1);
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
}
.model-tier-badge.high {
    background: rgba(224, 242, 233, 1);
    color: rgba(28, 116, 86, 1);
}
.model-tier-badge.medium {
    background: rgba(230, 238, 252, 1);
    color: rgba(44, 90, 168, 1);
}
.model-register-fields {
    display: grid;
    grid-template-columns: repeat(6, minmax(0, 1fr));
    gap: 10px 12px;
}
.model-register-fields label {
    grid-column: span 2;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
}
.model-register-fields label.wide {
    grid-column: span 3;
}
.model-register-fields span {
    font-size: 12px;
    color: rgba(95, 95, 95, 1);
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
    .model-register-fields {
        grid-template-columns: 1fr;
    }
    .model-register-fields label,
    .model-register-fields label.wide {
        grid-column: span 1;
    }
}
</style>
