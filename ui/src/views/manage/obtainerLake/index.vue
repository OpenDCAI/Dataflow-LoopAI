<template>
    <div class="dm-container">
        <div class="dm-major-container">
            <header class="lp-bar dm-title-block">
                <span class="lp-bar__title">DataMixer</span>
                <input
                    v-if="advancedMode"
                    v-model="lakePath"
                    class="lp-input lp-input--mono lake-input"
                    type="text"
                    placeholder=".datamixer/lake.yaml"
                    :title="local('Path to the lake config file')"
                    @keyup.enter="refresh"
                />
                <span v-else class="dm-workspace lp-truncate" :title="workspaceTitle">
                    {{ workspaceTitle }}
                </span>

                <div class="lp-bar__spacer"></div>

                <div class="lp-seg" role="tablist">
                    <button
                        v-for="view in views"
                        :key="view.key"
                        type="button"
                        role="tab"
                        class="lp-seg__item"
                        :class="{ 'is-active': activeView === view.key }"
                        :aria-selected="activeView === view.key"
                        @click="setActiveView(view.key)"
                    >
                        {{ view.label }}
                    </button>
                </div>

                <div class="lp-bar__sep"></div>

                <button
                    type="button"
                    class="lp-btn lp-btn--ghost lp-btn--icon"
                    :disabled="loading || rebuilding"
                    :title="local('Reload the DataMixer monitor data')"
                    @click="refresh"
                >
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round">
                        <path d="M20 12a8 8 0 1 1-2.3-5.6" />
                        <path d="M20 4v4h-4" />
                    </svg>
                </button>

                <template v-if="advancedMode">
                    <button
                        type="button"
                        class="lp-btn lp-btn--ghost"
                        :disabled="loading || rebuilding"
                        :title="local('Rebuild the monitor cache, may take a while')"
                        @click="rebuildMonitorCache"
                    >
                        {{ rebuilding ? local('Rebuilding') : local('Rebuild Cache') }}
                    </button>
                    <button
                        type="button"
                        class="lp-btn lp-btn--ghost"
                        :disabled="loading || rebuilding || probing || !monitor"
                        :title="local('Check embedding service availability')"
                        @click="probeEmbedding"
                    >
                        {{ probing ? local('Probing') : local('Probe') }}
                    </button>
                </template>

                <button
                    type="button"
                    class="lp-btn lp-btn--ghost lp-btn--icon"
                    :class="{ 'is-on': advancedMode }"
                    :title="local('Show advanced tools')"
                    @click="toggleAdvanced"
                >
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round">
                        <path d="M8 6l-4 6 4 6M16 6l4 6-4 6" />
                    </svg>
                </button>
            </header>

            <div class="dm-content-block">
                <div v-if="loading || rebuilding" class="monitor-loading-strip">
                    <div class="monitor-loading-copy">
                        <i class="ms-Icon ms-Icon--Sync"></i>
                        <span>{{ monitorProgressText }}</span>
                    </div>
                    <div class="monitor-progress">
                        <span></span>
                    </div>
                </div>

                <div v-if="error" class="error-banner">
                    <i class="ms-Icon ms-Icon--ErrorBadge"></i>
                    <span>{{ error }}</span>
                </div>

                <section v-if="activeView === 'lakes'" class="lake-dashboard">
                    <section class="dm-panel active-lake-panel">
                        <div class="panel-head">
                            <p>{{ local('Active Lake') }}</p>
                            <span>{{ activeLakeLabel }}</span>
                        </div>
                        <div class="active-lake-grid">
                            <div class="active-score">
                                <span>{{ formatNumber(activeLake?.samples || 0) }}</span>
                                <p>{{ local('records') }}</p>
                            </div>
                            <div class="active-meta">
                                <p :title="activeLake?.warehouse">{{ activeLake?.warehouse || lakeState?.warehouse || '-' }}</p>
                                <span>{{ formatNumber(activeLake?.datasets || 0) }} {{ local('datasets') }} · {{ activeLake?.source_type || 'pointer' }}</span>
                            </div>
                        </div>
                        <div class="lake-action-row">
                            <button
                                type="button"
                                :disabled="lakeActionRunning"
                                :title="local('Rescan the workspace for DataMixer lakes')"
                                @click="scanLakes"
                            >
                                <i class="ms-Icon ms-Icon--Refresh"></i>
                                <span>{{ lakeActionRunning ? local('Scanning') : local('Scan') }}</span>
                            </button>
                            <button
                                type="button"
                                class="secondary"
                                :disabled="lakeActionRunning"
                                :title="local('Disconnect the active lake (data is kept on disk)')"
                                @click="unloadActiveLake"
                            >
                                <i class="ms-Icon ms-Icon--PlugDisconnected"></i>
                                <span>{{ local('Unload') }}</span>
                            </button>
                        </div>
                    </section>

                    <section class="dm-panel lake-candidates-panel">
                        <div class="panel-head">
                            <p>{{ local('Discovered Lakes') }}</p>
                            <span>{{ lakeCandidates.length }} {{ local('candidates') }}</span>
                        </div>
                        <div class="lake-card-grid">
                            <article
                                v-for="lake in lakeCandidates"
                                :key="lake.id"
                                class="lake-card"
                                :class="{ active: lake.active, missing: !lake.warehouse_exists }"
                                @click="setDetail(lake.name, lake)"
                            >
                                <div class="lake-card-head">
                                    <div>
                                        <p>{{ lake.name || 'DataMixer Lake' }}</p>
                                        <span>{{ lake.source_type }}</span>
                                    </div>
                                    <strong>{{ lake.active ? local('Active') : lake.status }}</strong>
                                </div>
                                <div class="lake-card-metrics">
                                    <span>{{ formatNumber(lake.samples || 0) }} {{ local('records') }}</span>
                                    <span>{{ formatNumber(lake.datasets || 0) }} {{ local('datasets') }}</span>
                                    <span>{{ lake.status || '-' }}</span>
                                </div>
                                <p class="lake-path" :title="lake.warehouse">{{ lake.warehouse }}</p>
                                <div class="lake-card-actions">
                                    <button
                                        type="button"
                                        :disabled="lakeActionRunning || lake.active || !lake.warehouse_exists"
                                        :title="local('Load this lake as the active one')"
                                        @click.stop="loadScannedLake(lake)"
                                    >
                                        <i class="ms-Icon ms-Icon--OpenFolderHorizontal"></i>
                                        <span>{{ local('Load') }}</span>
                                    </button>
                                    <button type="button" class="secondary" @click.stop="setDetail(lake.name, lake)">
                                        {{ local('Details') }}
                                    </button>
                                </div>
                            </article>
                            <div v-if="lakeCandidates.length === 0" class="empty-lakes">
                                {{ lakeActionRunning ? `${local('Scanning')}...` : local('No DataMixer lakes found') }}
                            </div>
                        </div>
                    </section>
                </section>

                <dataset-manager
                    v-else-if="activeView === 'datasets'"
                    :lake-path="lakePath"
                    :warehouse="activeWarehouse"
                />

                <template v-else>
                <section class="lake-runtime-panel" :class="runtimeLevel">
                    <div class="runtime-primary">
                        <div class="runtime-icon">
                            <i class="ms-Icon" :class="`ms-Icon--${runtimeIcon}`"></i>
                        </div>
                        <div class="runtime-copy">
                            <p class="runtime-title">{{ runtimeTitle }}</p>
                            <p class="runtime-subtitle" :title="runtimeSubtitle">{{ runtimeSubtitle }}</p>
                        </div>
                    </div>
                    <div class="runtime-states">
                        <button
                            v-for="item in runtimeCards"
                            :key="item.key"
                            type="button"
                            class="runtime-state"
                            :class="item.level"
                            @click="setDetail(item.label, item.payload)"
                        >
                            <span>{{ item.label }}</span>
                            <strong>{{ item.value }}</strong>
                            <small>{{ item.note }}</small>
                        </button>
                    </div>
                    <div class="runtime-actions">
                        <button type="button" class="secondary" @click="setDetail('Lake Runtime', runtimeDetail)">
                            <i class="ms-Icon ms-Icon--Info"></i>
                            <span>{{ local('Details') }}</span>
                        </button>
                        <button
                            type="button"
                            :disabled="loading || rebuilding"
                            :class="{ attention: cacheActionRequired }"
                            @click="rebuildMonitorCache"
                        >
                            <i class="ms-Icon ms-Icon--Sync"></i>
                            <span>{{ rebuilding ? local('Rebuilding') : local('Rebuild Cache') }}</span>
                        </button>
                    </div>
                </section>

                <web-pipeline-status
                    :lake="lakePath"
                    :root="webPipelineRoot"
                    embedded
                ></web-pipeline-status>

                <section v-if="advancedMode" class="surface-panel">
                    <div
                        v-for="item in operationSurface"
                        :key="item.key"
                        class="surface-item"
                        :class="item.state"
                        @click="applyCommandTemplate(item.command)"
                    >
                        <span class="surface-dot"></span>
                        <div>
                            <p>{{ item.label }}</p>
                            <span>{{ item.note }}</span>
                        </div>
                    </div>
                </section>

                <section v-if="advancedMode" class="dm-panel command-panel">
                    <div class="panel-head">
                        <p>{{ local('DataMixer Command Surface') }}</p>
                        <span>{{ commandEndpointLabel }}</span>
                    </div>
                    <div class="command-layout">
                        <div class="command-groups">
                            <div v-for="group in commandGroups" :key="group.key" class="command-group">
                                <p>{{ group.label }}</p>
                                <div class="command-chips">
                                    <button
                                        v-for="command in group.commands"
                                        :key="`${group.key}-${command}`"
                                        type="button"
                                        @click="applyCommandTemplate(command)"
                                    >
                                        {{ command }}
                                    </button>
                                </div>
                            </div>
                        </div>
                        <div class="command-runner">
                            <textarea
                                v-model="commandLine"
                                spellcheck="false"
                                placeholder="lake current"
                                @keydown.ctrl.enter.prevent="runCommand"
                            ></textarea>
                            <div class="command-actions">
                                <button type="button" :disabled="commandRunning" @click="runCommand">
                                    <i class="ms-Icon ms-Icon--Play"></i>
                                    <span>{{ commandRunning ? local('Running') : local('Run') }}</span>
                                </button>
                                <button type="button" class="secondary" @click="applyCommandTemplate('lake current')">
                                    lake current
                                </button>
                                <button type="button" class="secondary" @click="applyCommandTemplate('lineage list')">
                                    lineage
                                </button>
                            </div>
                            <pre>{{ commandResultText }}</pre>
                        </div>
                    </div>
                </section>

                <div class="kpi-grid">
                    <button
                        v-for="item in kpis"
                        :key="item.key"
                        class="kpi-card"
                        type="button"
                        @click="setDetail(item.label, item.payload)"
                    >
                        <div class="kpi-icon" :style="{ background: item.background, color: item.color }">
                            <i class="ms-Icon" :class="`ms-Icon--${item.icon}`"></i>
                        </div>
                        <div class="kpi-copy">
                            <p class="kpi-label">{{ item.label }}</p>
                            <p class="kpi-value">{{ item.value }}</p>
                            <p class="kpi-note">{{ item.note }}</p>
                        </div>
                    </button>
                </div>

                <section class="dm-panel benchmark-panel">
                    <div class="panel-head">
                        <p>{{ local('Benchmark Guard') }}</p>
                        <span>{{ formatNumber(benchmarks.count || 0) }} {{ local('sets') }} · {{ formatNumber(benchmarks.total_rows || 0) }} {{ local('rows') }}</span>
                    </div>
                    <div class="benchmark-layout">
                        <div class="benchmark-score">
                            <strong>{{ formatNumber(benchmarks.count || 0) }}</strong>
                            <span>{{ local('registered benchmark sets') }}</span>
                        </div>
                        <div class="benchmark-table">
                            <button
                                v-for="item in benchmarkSets"
                                :key="item.name"
                                type="button"
                                class="benchmark-item"
                                @click="setDetail(item.name, item)"
                            >
                                <span :title="item.name">{{ item.name }}</span>
                                <strong>{{ formatNumber(item.rows || item.num_texts || 0) }}</strong>
                            </button>
                            <div v-if="benchmarkSets.length === 0" class="benchmark-empty">
                                {{ local('No benchmark guard sets registered') }}
                            </div>
                        </div>
                    </div>
                </section>

                <div class="main-grid">
                    <section class="dm-panel warehouse-panel">
                        <div class="panel-head">
                            <p>{{ local('Warehouse') }}</p>
                            <span>{{ tableRows.length }} {{ local('views') }}</span>
                        </div>
                        <div class="warehouse-layout">
                            <div class="config-grid">
                                <button
                                    v-for="item in configItems"
                                    :key="item.key"
                                    type="button"
                                    class="config-tile"
                                    @click="setDetail(item.label, item)"
                                >
                                    <span>{{ item.label }}</span>
                                    <p :title="item.value">{{ item.value || '-' }}</p>
                                </button>
                            </div>
                            <div class="table-health">
                                <button
                                    v-for="table in tableRows"
                                    :key="table.name"
                                    class="table-row"
                                    type="button"
                                    @click="setDetail(table.name, table)"
                                >
                                    <span class="table-status" :class="{ ok: table.exists, warn: !table.exists }"></span>
                                    <span class="table-name">{{ table.name }}</span>
                                    <span class="table-count">{{ formatNumber(table.count) }}</span>
                                    <span class="table-size">{{ formatBytes(table.size_bytes) }}</span>
                                    <span class="table-time">{{ formatTime(table.modified_at) }}</span>
                                </button>
                            </div>
                        </div>
                    </section>

                    <section class="dm-panel ingest-panel">
                        <div class="panel-head">
                            <p>{{ local('Ingest Runs') }}</p>
                            <span>{{ ingestRows.length }} {{ local('recent') }}</span>
                        </div>
                        <base-chart type="bar" :chart-data="ingestTrendChart" :options="trendOptions"></base-chart>
                    </section>
                </div>

                <div class="insight-grid">
                    <section class="dm-panel">
                        <div class="panel-head">
                            <p>{{ local('Composition') }}</p>
                            <div class="segmented-control">
                                <button
                                    v-for="mode in compositionModes"
                                    :key="mode.key"
                                    type="button"
                                    :class="{ active: compositionMode === mode.key }"
                                    @click="compositionMode = mode.key"
                                >
                                    {{ mode.label }}
                                </button>
                            </div>
                        </div>
                        <base-chart
                            type="doughnut"
                            :chart-data="compositionChart"
                            :options="doughnutOptions"
                        ></base-chart>
                    </section>

                    <section class="dm-panel">
                        <div class="panel-head">
                            <p>{{ local('Top Tags') }}</p>
                            <span>{{ topTags.length }} {{ local('values') }}</span>
                        </div>
                        <base-chart type="bar" :chart-data="tagChart" :options="horizontalBarOptions"></base-chart>
                    </section>

                    <section class="dm-panel embedding-panel">
                        <div class="panel-head">
                            <p>{{ local('Embedding') }}</p>
                            <span>{{ formatPercent(embedding.coverage) }}</span>
                        </div>
                        <div class="embedding-layout">
                            <base-chart
                                type="doughnut"
                                :chart-data="embeddingChart"
                                :options="doughnutOptions"
                            ></base-chart>
                            <div class="embedding-meta">
                                <p><span>{{ local('Model') }}</span>{{ embedding.embedding_model || '-' }}</p>
                                <p><span>{{ local('Backend') }}</span>{{ embedding.embedding_backend || '-' }}</p>
                                <p><span>{{ local('Provider') }}</span>{{ embedding.embedding_provider || '-' }}</p>
                                <p><span>{{ local('Probe') }}</span>{{ embedding.probe?.status || 'not_checked' }}</p>
                            </div>
                        </div>
                    </section>
                </div>

                <div class="detail-grid" :class="{ simple: !advancedMode }">
                    <section class="dm-panel quality-panel">
                        <div class="panel-head">
                            <p>{{ local('Quality Findings') }}</p>
                            <span>{{ formatNumber(summary.quality_findings || 0) }} {{ local('items') }}</span>
                        </div>
                        <base-chart type="bar" :chart-data="qualityChart" :options="stackedOptions"></base-chart>
                    </section>

                    <aside v-if="advancedMode" class="dm-panel inspect-panel">
                        <div class="panel-head">
                            <p>{{ detailTitle }}</p>
                            <span>JSON</span>
                        </div>
                        <pre>{{ detailText }}</pre>
                    </aside>
                </div>

                <section class="dm-panel latest-panel">
                    <div class="panel-head">
                        <p>{{ local('Latest DataMixer Rows') }}</p>
                        <div class="segmented-control">
                            <button
                                v-for="tab in tabs"
                                :key="tab.key"
                                type="button"
                                :class="{ active: activeTab === tab.key }"
                                @click="activeTab = tab.key"
                            >
                                {{ tab.label }}
                            </button>
                        </div>
                    </div>
                    <div class="preview-table">
                        <table>
                            <thead>
                                <tr>
                                    <th v-for="column in activeColumns" :key="column.key">
                                        {{ column.label }}
                                    </th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr
                                    v-for="(row, index) in activeRows"
                                    :key="`${activeTab}-${index}`"
                                    @click="setDetail(activeTab, row)"
                                >
                                    <td v-for="column in activeColumns" :key="column.key">
                                        {{ formatCell(row[column.key]) }}
                                    </td>
                                </tr>
                                <tr v-if="activeRows.length === 0">
                                    <td :colspan="activeColumns.length || 1">{{ local('No Data') }}</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </section>
                </template>
            </div>
        </div>
    </div>
</template>

<script>
import { mapState, mapActions } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'
import { useTheme } from '@/stores/theme'
import baseChart from '@/components/manage/obtainerLake/baseChart.vue'
import webPipelineStatus from '@/components/manage/obtainerLake/webPipelineStatus.vue'
import datasetManager from '@/components/manage/obtainerLake/datasetManager.vue'
import {
    deleteDataMixerLake,
    getDataMixerCommands,
    getDataMixerEmbeddingHealth,
    getDataMixerMonitor,
    loadDataMixerLake,
    rebuildDataMixerMonitor,
    runDataMixerCli,
    scanDataMixerLakes
} from '@/services/obtainerLake'

export default {
    components: {
        baseChart,
        webPipelineStatus,
        datasetManager
    },
    data() {
        return {
            lakePath: '.datamixer/lake.yaml',
            monitor: null,
            lakeState: {},
            lakeScan: null,
            loading: false,
            rebuilding: false,
            probing: false,
            error: '',
            activeView: 'workbench',
            advancedMode: false,
            activeTab: 'records',
            compositionMode: 'quality_level',
            detail: null,
            commandGroups: [],
            commandEndpoint: 'loopai-obtainercli dm',
            commandLine: 'lake current',
            commandRunning: false,
            commandResult: null,
            lakeActionRunning: false,
            pendingWorkbenchRefresh: false,
            monitorRefreshStartedAt: 0,
            monitorElapsedSeconds: 0,
            monitorProgressTimer: null,
            rebuildPollTimer: null
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local', 'language']),
        ...mapState(useTheme, ['gradient']),
        summary() {
            return this.monitor?.summary || {}
        },
        embedding() {
            return this.monitor?.embedding || { probe: { status: 'not_checked' } }
        },
        benchmarks() {
            return this.monitor?.benchmarks || { count: 0, total_rows: 0, sets: [] }
        },
        benchmarkSets() {
            const sets = this.benchmarks?.sets || []
            return Array.isArray(sets) ? sets : []
        },
        warnings() {
            return this.monitor?.warnings || []
        },
        views() {
            return [
                { key: 'workbench', label: this.local('Workbench') },
                { key: 'datasets', label: this.local('Datasets') },
                { key: 'lakes', label: this.local('Lake Management') }
            ]
        },
        lakeRoot() {
            return this.monitor?.lake_root || ''
        },
        webPipelineRoot() {
            return this.monitor?.config?.warehouse || this.lakeState?.warehouse || ''
        },
        workspaceTitle() {
            if (this.monitor?.lake_root) return `${this.monitor.lake_root} - ${this.monitor.lake_config || this.lakePath}`
            return this.lakePath
        },
        activeWarehouse() {
            return this.monitor?.warehouse || this.monitor?.config?.warehouse || this.lakeState?.warehouse || this.activeLake?.warehouse || ''
        },
        cacheStatus() {
            return this.monitor?.cache_status || (this.monitor ? 'fresh' : 'missing')
        },
        cacheLevel() {
            const status = this.cacheStatus
            if (status === 'fresh') return 'good'
            if (status === 'rebuilding') return 'running'
            if (status === 'cache_missing' || status === 'stale') return 'warn'
            if (status === 'error') return 'bad'
            return 'missing'
        },
        rebuildState() {
            return this.monitor?.rebuild || { status: 'idle', job_id: '', message: '' }
        },
        rebuildLevel() {
            const status = String(this.rebuildState.status || 'idle').toLowerCase()
            if (['queued', 'running', 'rebuilding'].includes(status)) return 'running'
            if (status === 'error') return 'bad'
            return 'good'
        },
        lakePointerLevel() {
            if (!this.monitor && this.activeLakeLabel === 'missing') return 'missing'
            if (!this.activeWarehouse) return 'missing'
            if (this.monitor?.stale && this.cacheStatus !== 'rebuilding') return 'warn'
            return 'good'
        },
        embeddingLevel() {
            const probeStatus = String(this.embedding?.probe?.status || '').toLowerCase()
            if (probeStatus.includes('error') || probeStatus.includes('fail')) return 'bad'
            if (Number(this.embedding.pending_records || 0) > 0) return 'running'
            if (this.embedding?.auto_embed === 'true' || this.embedding?.embedding_model) return 'good'
            return 'missing'
        },
        runtimeLevel() {
            if (!this.monitor && this.activeLakeLabel === 'missing') return 'missing'
            if (this.cacheLevel === 'bad' || this.rebuildLevel === 'bad') return 'bad'
            if (this.cacheLevel === 'running' || this.rebuildLevel === 'running') return 'running'
            if (this.cacheLevel === 'warn' || this.lakePointerLevel === 'warn') return 'warn'
            return 'good'
        },
        runtimeIcon() {
            if (this.runtimeLevel === 'bad') return 'ErrorBadge'
            if (this.runtimeLevel === 'running') return 'Sync'
            if (this.runtimeLevel === 'warn') return 'Warning'
            if (this.runtimeLevel === 'missing') return 'PlugDisconnected'
            return 'Database'
        },
        runtimeTitle() {
            if (!this.monitor && this.activeLakeLabel === 'missing') return this.local('No DataMixer lake loaded')
            if (this.cacheStatus === 'cache_missing') return this.local('Lake monitor cache is missing')
            if (this.cacheStatus === 'rebuilding') return this.local('Lake monitor cache is rebuilding')
            if (this.cacheStatus === 'error') return this.local('Lake monitor cache failed')
            if (this.monitor?.stale) return this.local('Lake monitor cache is stale')
            if (this.warnings.length > 0) return this.local('Lake runtime has warnings')
            return this.local('Lake runtime is ready')
        },
        runtimeSubtitle() {
            if (!this.monitor) return this.local('Load a DataMixer lake or scan available warehouses.')
            const parts = [
                `${this.local('Lake')} ${this.monitor.lake_config || this.lakePath}`,
                `${this.local('Cache')} ${this.cacheStatus}`,
                `${this.local('Updated')} ${this.formatTime(this.monitor.updated_at || this.monitor.refreshed_at)}`
            ]
            if (this.monitor.stale_reason) parts.push(this.monitor.stale_reason)
            return parts.join(' · ')
        },
        cacheActionRequired() {
            return ['cache_missing', 'stale', 'error'].includes(this.cacheStatus)
        },
        runtimeCards() {
            return [
                {
                    key: 'lake',
                    label: this.local('Lake'),
                    value: this.activeLakeLabel,
                    note: this.shortPath(this.activeWarehouse || this.lakePath),
                    level: this.lakePointerLevel,
                    payload: {
                        lake_config: this.monitor?.lake_config || this.lakePath,
                        lake_root: this.monitor?.lake_root || this.activeLake?.lake_root || '',
                        warehouse: this.activeWarehouse,
                        state: this.lakeState,
                        active: this.activeLake
                    }
                },
                {
                    key: 'cache',
                    label: this.local('Cache'),
                    value: this.cacheStatus,
                    note: this.monitor?.stale_reason || this.formatTime(this.monitor?.updated_at || this.monitor?.refreshed_at),
                    level: this.cacheLevel,
                    payload: {
                        cache_status: this.cacheStatus,
                        stale: Boolean(this.monitor?.stale),
                        stale_reason: this.monitor?.stale_reason || '',
                        signature: this.monitor?.signature || {},
                        catalog_db_size: this.monitor?.catalog_db_size,
                        catalog_db_wal_size: this.monitor?.catalog_db_wal_size,
                        audit_signature: this.monitor?.audit_signature || {}
                    }
                },
                {
                    key: 'rebuild',
                    label: this.local('Rebuild'),
                    value: this.rebuildState.status || 'idle',
                    note: this.rebuildState.message || this.rebuildState.job_id || '-',
                    level: this.rebuildLevel,
                    payload: this.rebuildState
                },
                {
                    key: 'embedding',
                    label: this.local('Embedding'),
                    value: this.formatPercent(this.embedding.coverage || 0),
                    note: `${this.formatNumber(this.embedding.pending_records || 0)} ${this.local('pending')}`,
                    level: this.embeddingLevel,
                    payload: this.embedding
                },
                {
                    key: 'benchmark',
                    label: this.local('Benchmark'),
                    value: `${this.formatNumber(this.benchmarks.count || 0)} ${this.local('sets')}`,
                    note: `${this.formatNumber(this.benchmarks.total_rows || 0)} ${this.local('rows')}`,
                    level: Number(this.benchmarks.count || 0) > 0 ? 'good' : 'warn',
                    payload: this.benchmarks
                }
            ]
        },
        runtimeDetail() {
            return {
                title: this.runtimeTitle,
                lake: this.runtimeCards[0]?.payload,
                cache: this.runtimeCards[1]?.payload,
                rebuild: this.rebuildState,
                embedding: this.embedding,
                summary: this.summary,
                warnings: this.warnings
            }
        },
        operationSurface() {
            return [
                {
                    key: 'monitor',
                    label: this.local('Monitor'),
                    note: 'GET /obtainer/lake/monitor',
                    state: 'available',
                    command: 'lake current'
                },
                {
                    key: 'embedding',
                    label: this.local('Embedding Probe'),
                    note: 'index stats',
                    state: 'available',
                    command: 'index stats'
                },
                {
                    key: 'ingest',
                    label: this.local('Ingest / Agent Ingest'),
                    note: 'POST /obtainer/datamixer/cli',
                    state: 'available',
                    command: 'ingest DATASET --file /path/to/input.jsonl --dataset-card /path/to/DATASET.md --source-row-count N --derived-field FIELD'
                },
                {
                    key: 'recipe',
                    label: this.local('Recipe Export'),
                    note: 'recipe export --snapshot',
                    state: 'available',
                    command: 'recipe plan /path/to/recipe.yaml'
                }
            ]
        },
        commandEndpointLabel() {
            return this.commandEndpoint || 'loopai-obtainercli dm'
        },
        commandResultText() {
            return JSON.stringify(
                this.commandResult || { message: this.local('Run a DataMixer command to inspect its JSON result.') },
                null,
                2
            )
        },
        monitorProgressText() {
            const seconds = Math.max(Number(this.monitorElapsedSeconds || 0), 0)
            if (this.rebuilding) return `${this.local('Rebuilding monitor cache')} · ${seconds}s`
            return `${this.local('Refreshing monitor')} · ${seconds}s`
        },
        lakeCandidates() {
            return this.lakeScan?.lakes || []
        },
        activeLake() {
            return this.lakeCandidates.find((lake) => lake.active) || null
        },
        activeLakeLabel() {
            if (this.activeLake?.status) return this.activeLake.status
            return this.lakeState?.status || 'missing'
        },
        kpis() {
            return [
                {
                    key: 'records',
                    label: this.local('Records'),
                    value: this.formatNumber(this.summary.records || 0),
                    note: `${this.formatNumber(this.summary.datasets || 0)} ${this.local('datasets')}`,
                    icon: 'Database',
                    background: 'rgba(64, 99, 170, 0.12)',
                    color: 'rgba(64, 99, 170, 1)',
                    payload: this.summary
                },
                {
                    key: 'embedding',
                    label: this.local('Embedding Coverage'),
                    value: this.formatPercent(this.summary.embedding_coverage || 0),
                    note: `${this.formatNumber(this.embedding.pending_records || 0)} ${this.local('pending')}`,
                    icon: 'LightningBolt',
                    background: 'rgba(20, 145, 116, 0.12)',
                    color: 'rgba(20, 120, 96, 1)',
                    payload: this.embedding
                },
                {
                    key: 'quality',
                    label: this.local('Quality Findings'),
                    value: this.formatNumber(this.summary.quality_findings || 0),
                    note: `${this.warnings.length} ${this.local('warnings')}`,
                    icon: 'Diagnostic',
                    background: 'rgba(203, 101, 36, 0.14)',
                    color: 'rgba(181, 83, 20, 1)',
                    payload: this.monitor?.latest?.quality_findings || []
                },
                {
                    key: 'benchmark',
                    label: this.local('Benchmark Guard'),
                    value: this.formatNumber(this.benchmarks.count || 0),
                    note: `${this.formatNumber(this.benchmarks.total_rows || 0)} ${this.local('rows')}`,
                    icon: 'Shield',
                    background: 'rgba(185, 72, 83, 0.12)',
                    color: 'rgba(154, 52, 62, 1)',
                    payload: this.benchmarks
                },
                {
                    key: 'exports',
                    label: this.local('Exports'),
                    value: this.formatNumber(this.summary.exports || 0),
                    note: `${this.formatNumber(this.summary.ingest_runs || 0)} ${this.local('ingest runs')}`,
                    icon: 'Upload',
                    background: 'rgba(144, 91, 166, 0.12)',
                    color: 'rgba(116, 65, 145, 1)',
                    payload: this.monitor?.latest?.exports || []
                }
            ]
        },
        configItems() {
            const config = this.monitor?.config || {}
            return [
                { key: 'lake_config', label: this.local('Lake Config'), value: this.monitor?.lake_config || this.lakePath },
                { key: 'lake_root', label: this.local('Warehouse Root'), value: this.lakeRoot },
                { key: 'catalog', label: this.local('Catalog'), value: config.catalog || 'datamixer' },
                { key: 'namespace', label: this.local('Namespace'), value: config.namespace || '-' },
                { key: 'warehouse', label: this.local('Warehouse'), value: config.warehouse || '-' }
            ]
        },
        tableRows() {
            return Object.values(this.monitor?.tables || {}).sort((a, b) => String(a.name).localeCompare(String(b.name)))
        },
        ingestRows() {
            return this.monitor?.charts?.ingest_trend || []
        },
        topTags() {
            return this.monitor?.charts?.top_tags || []
        },
        palette() {
            return [
                'rgba(64, 99, 170, 0.82)',
                'rgba(20, 145, 116, 0.82)',
                'rgba(203, 101, 36, 0.82)',
                'rgba(144, 91, 166, 0.82)',
                'rgba(218, 165, 55, 0.86)',
                'rgba(69, 148, 163, 0.82)',
                'rgba(185, 72, 83, 0.82)',
                'rgba(104, 121, 141, 0.82)'
            ]
        },
        compositionModes() {
            return [
                { key: 'quality_level', label: this.local('Quality Level') },
                { key: 'processing_level', label: this.local('Processing Level') },
                { key: 'domain', label: this.local('Domain') },
                { key: 'source_kind', label: this.local('Source') },
                { key: 'task_type', label: this.local('Task') }
            ]
        },
        ingestTrendChart() {
            const labels = this.ingestRows.map((row) => row.label || row.finished_at || '-')
            return {
                labels,
                datasets: [
                    {
                        label: this.local('Seen'),
                        data: this.ingestRows.map((row) => row.rows_seen || 0),
                        backgroundColor: 'rgba(104, 121, 141, 0.5)',
                        borderRadius: 4
                    },
                    {
                        label: this.local('Written'),
                        data: this.ingestRows.map((row) => row.rows_written || 0),
                        backgroundColor: 'rgba(20, 145, 116, 0.72)',
                        borderRadius: 4
                    },
                    {
                        label: this.local('Quarantined'),
                        data: this.ingestRows.map((row) => row.rows_quarantined || 0),
                        backgroundColor: 'rgba(185, 72, 83, 0.72)',
                        borderRadius: 4
                    }
                ]
            }
        },
        compositionChart() {
            const data = this.monitor?.charts?.composition?.[this.compositionMode] || {}
            const labels = Object.keys(data)
            return {
                labels,
                datasets: [
                    {
                        data: labels.map((label) => data[label]),
                        backgroundColor: labels.map((_, index) => this.palette[index % this.palette.length]),
                        borderWidth: 0
                    }
                ]
            }
        },
        tagChart() {
            const rows = this.topTags.slice(0, 10)
            return {
                labels: rows.map((row) => `${row.tag_name}=${row.tag_value}`),
                datasets: [
                    {
                        label: this.local('Records'),
                        data: rows.map((row) => row.count),
                        backgroundColor: 'rgba(64, 99, 170, 0.72)',
                        borderRadius: 4
                    }
                ]
            }
        },
        qualityChart() {
            const rows = this.monitor?.charts?.quality_findings || []
            const labels = [...new Set(rows.map((row) => row.finding_type || 'quality_signal'))]
            const severities = [...new Set(rows.map((row) => row.severity || 'info'))]
            return {
                labels,
                datasets: severities.map((severity) => ({
                    label: severity,
                    data: labels.map((label) => {
                        const found = rows.find(
                            (row) =>
                                (row.finding_type || 'quality_signal') === label &&
                                (row.severity || 'info') === severity
                        )
                        return found ? found.count : 0
                    }),
                    backgroundColor: this.severityColor(severity),
                    borderRadius: 4
                }))
            }
        },
        embeddingChart() {
            return {
                labels: [this.local('Indexed'), this.local('Pending')],
                datasets: [
                    {
                        data: [this.embedding.indexed_records || 0, this.embedding.pending_records || 0],
                        backgroundColor: ['rgba(20, 145, 116, 0.78)', 'rgba(203, 101, 36, 0.45)'],
                        borderWidth: 0
                    }
                ]
            }
        },
        trendOptions() {
            return {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { position: 'bottom', labels: { boxWidth: 10 } }
                },
                scales: {
                    x: { grid: { display: false } },
                    y: { beginAtZero: true, ticks: { precision: 0 } }
                }
            }
        },
        horizontalBarOptions() {
            return {
                ...this.trendOptions,
                indexAxis: 'y',
                plugins: {
                    legend: { display: false }
                }
            }
        },
        stackedOptions() {
            return {
                ...this.trendOptions,
                scales: {
                    x: { stacked: true, grid: { display: false } },
                    y: { stacked: true, beginAtZero: true, ticks: { precision: 0 } }
                }
            }
        },
        doughnutOptions() {
            return {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '64%',
                plugins: {
                    legend: { position: 'bottom', labels: { boxWidth: 10 } }
                }
            }
        },
        tabs() {
            return [
                { key: 'records', label: this.local('Records') },
                { key: 'ingest_runs', label: this.local('Runs') },
                { key: 'quality_findings', label: this.local('Quality') },
                { key: 'exports', label: this.local('Exports') },
                { key: 'warnings', label: this.local('Warnings') }
            ]
        },
        columns() {
            return {
                records: [
                    { key: 'record_id', label: this.local('Record') },
                    { key: 'dataset_id', label: this.local('Dataset') },
                    { key: 'quality_level', label: this.local('Quality Level') },
                    { key: 'processing_level', label: this.local('Processing Level') },
                    { key: 'source_kind', label: this.local('Source') },
                    { key: 'text', label: this.local('Text') }
                ],
                ingest_runs: [
                    { key: 'ingest_run_id', label: this.local('Run') },
                    { key: 'status', label: this.local('Status') },
                    { key: 'rows_seen', label: this.local('Seen') },
                    { key: 'rows_written', label: this.local('Written') },
                    { key: 'finished_at', label: this.local('Finished') }
                ],
                quality_findings: [
                    { key: 'finding_type', label: this.local('Type') },
                    { key: 'severity', label: this.local('Severity') },
                    { key: 'score', label: this.local('Score') },
                    { key: 'detector', label: this.local('Detector') },
                    { key: 'created_at', label: this.local('Created') }
                ],
                exports: [
                    { key: 'export_id', label: this.local('Export') },
                    { key: 'strategy', label: this.local('Strategy') },
                    { key: 'actual_size', label: this.local('Rows') },
                    { key: 'format', label: this.local('Format') },
                    { key: 'created_at', label: this.local('Created') }
                ],
                warnings: [
                    { key: 'code', label: this.local('Code') },
                    { key: 'table', label: this.local('Table') },
                    { key: 'line', label: this.local('Line') },
                    { key: 'message', label: this.local('Message') }
                ]
            }
        },
        activeColumns() {
            return this.columns[this.activeTab] || []
        },
        activeRows() {
            if (this.activeTab === 'warnings') return this.warnings
            return this.monitor?.latest?.[this.activeTab] || []
        },
        detailTitle() {
            return this.detail?.title || this.local('DataMixer Summary')
        },
        detailText() {
            return JSON.stringify(this.detail?.payload || this.summary || {}, null, 2)
        }
    },
    watch: {
        '$route.path'() {
            this.activeView = this.routeView()
            if (this.activeView === 'workbench' && (this.pendingWorkbenchRefresh || !this.monitor)) {
                this.refresh({ force: true })
            }
        }
    },
    mounted() {
        this.activeView = this.routeView()
        try {
            this.advancedMode = localStorage.getItem('dm-advanced-mode') === '1'
        } catch (error) {}
        this.loadCommandSurface()
        this.scanLakes({ silent: true })
        if (this.activeView === 'workbench') {
            this.refresh()
        }
    },
    beforeUnmount() {
        this.stopMonitorProgress()
        this.stopRebuildPolling()
    },
    methods: {
        ...mapActions(useAppConfig, ['setLanguage']),
        toggleAdvanced() {
            this.advancedMode = !this.advancedMode
            try {
                localStorage.setItem('dm-advanced-mode', this.advancedMode ? '1' : '0')
            } catch (error) {}
        },
        routeView() {
            const path = String(this.$route?.path || '')
            if (path.includes('/datamixer/datasets')) return 'datasets'
            if (path.includes('/datamixer/lakes')) return 'lakes'
            return 'workbench'
        },
        setActiveView(view) {
            this.activeView = view
            const target = view === 'lakes'
                ? '/m/datamixer/lakes'
                : view === 'datasets'
                    ? '/m/datamixer/datasets'
                    : '/m/datamixer'
            if (this.$route?.path !== target) {
                this.$router?.push(target).catch(() => {})
            }
            if (view === 'workbench' && (this.pendingWorkbenchRefresh || !this.monitor)) {
                this.refresh({ force: true })
            }
        },
        async loadCommandSurface() {
            try {
                const res = await getDataMixerCommands()
                if (res?.code && res.code !== 200) return
                this.commandGroups = res.data?.groups || []
                this.commandEndpoint = res.data?.entrypoint || this.commandEndpoint
            } catch (error) {
                this.commandResult = {
                    error: error?.response?.data?.message || error?.message || 'Failed to load DataMixer commands'
                }
            }
        },
        startMonitorProgress() {
            this.stopMonitorProgress()
            this.monitorRefreshStartedAt = Date.now()
            this.monitorElapsedSeconds = 0
            this.monitorProgressTimer = window.setInterval(() => {
                this.monitorElapsedSeconds = Math.floor((Date.now() - this.monitorRefreshStartedAt) / 1000)
            }, 1000)
        },
        stopMonitorProgress() {
            if (this.monitorProgressTimer) {
                window.clearInterval(this.monitorProgressTimer)
                this.monitorProgressTimer = null
            }
        },
        stopRebuildPolling() {
            if (this.rebuildPollTimer) {
                window.clearTimeout(this.rebuildPollTimer)
                this.rebuildPollTimer = null
            }
        },
        async refresh(options = {}) {
            if (this.loading && !options.force) return
            if (this.loading && options.force) return
            if (this.rebuilding && !options.allowDuringRebuild) return
            this.loading = true
            this.error = ''
            this.startMonitorProgress()
            try {
                await this.scanLakes({ silent: true })
                const res = await getDataMixerMonitor({ lake: this.lakePath })
                if (res?.code && res.code !== 200) {
                    this.error = res.message || 'Failed to load DataMixer monitor'
                    return
                }
                this.monitor = res.data || null
                this.detail = { title: 'DataMixer Summary', payload: res.data?.summary || {} }
                this.pendingWorkbenchRefresh = false
            } catch (error) {
                this.error = error?.response?.data?.message || error?.message || 'Failed to load DataMixer monitor'
            } finally {
                this.loading = false
                this.stopMonitorProgress()
            }
        },
        async rebuildMonitorCache() {
            if (this.rebuilding || this.loading) return
            this.rebuilding = true
            this.error = ''
            this.startMonitorProgress()
            try {
                const res = await rebuildDataMixerMonitor({ lake: this.lakePath })
                if (res?.code && res.code !== 200) {
                    this.error = res.message || 'Failed to rebuild DataMixer monitor cache'
                    return
                }
                this.setDetail('Monitor Rebuild', res.data)
                await this.pollMonitorRebuild()
            } catch (error) {
                this.error = error?.response?.data?.message || error?.message || 'Failed to rebuild DataMixer monitor cache'
            } finally {
                this.rebuilding = false
                this.stopRebuildPolling()
                this.stopMonitorProgress()
            }
        },
        pollMonitorRebuild() {
            return new Promise((resolve) => {
                let attempts = 0
                const tick = async () => {
                    attempts += 1
                    try {
                        const res = await getDataMixerMonitor({ lake: this.lakePath })
                        if (!res?.code || res.code === 200) {
                            this.monitor = res.data || null
                            this.detail = { title: 'DataMixer Summary', payload: res.data?.summary || {} }
                            const cacheStatus = res.data?.cache_status || ''
                            const rebuildStatus = res.data?.rebuild?.status || ''
                            if (cacheStatus === 'fresh' || cacheStatus === 'error' || rebuildStatus === 'error') {
                                if (cacheStatus === 'error' || rebuildStatus === 'error') {
                                    this.error = res.data?.stale_reason || res.data?.rebuild?.message || 'Monitor rebuild failed'
                                }
                                resolve()
                                return
                            }
                        }
                    } catch (error) {
                        this.error = error?.response?.data?.message || error?.message || 'Failed to poll monitor rebuild'
                        resolve()
                        return
                    }
                    if (attempts >= 60) {
                        this.error = 'Monitor rebuild is still running. Refresh again later.'
                        resolve()
                        return
                    }
                    this.rebuildPollTimer = window.setTimeout(tick, 1000)
                }
                tick()
            })
        },
        async scanLakes(options = {}) {
            if (this.lakeActionRunning && !options.silent) return
            this.lakeActionRunning = true
            if (!options.silent) this.error = ''
            try {
                const res = await scanDataMixerLakes({ lake: this.lakePath })
                if (res?.code && res.code !== 200) {
                    if (!options.silent) this.error = res.message || 'Failed to scan DataMixer lakes'
                    return
                }
                this.lakeScan = res.data || null
                this.lakeState = res.data?.active || this.lakeState || {}
            } catch (error) {
                if (!options.silent) {
                    this.error = error?.response?.data?.message || error?.message || 'Failed to scan DataMixer lakes'
                }
            } finally {
                this.lakeActionRunning = false
            }
        },
        async loadScannedLake(lake) {
            if (!lake?.warehouse || this.lakeActionRunning) return
            this.lakeActionRunning = true
            this.error = ''
            try {
                const res = await loadDataMixerLake({
                    link: this.lakePath,
                    warehouse: lake.warehouse,
                    lake_root: lake.lake_root || undefined
                })
                if (res?.code && res.code !== 200) {
                    this.error = res.message || 'Failed to load DataMixer lake'
                    this.setDetail('Lake Load Error', res)
                    return
                }
                this.setDetail('Lake Load Result', res.data)
                if (this.lakeScan?.lakes) {
                    this.lakeScan.lakes = this.lakeScan.lakes.map((item) => ({
                        ...item,
                        active: item.warehouse === lake.warehouse
                    }))
                }
                this.lakeState = {
                    ...(res.data || {}),
                    status: 'loaded',
                    warehouse: lake.warehouse,
                    warehouse_exists: true
                }
                this.monitor = null
                this.pendingWorkbenchRefresh = true
            } catch (error) {
                const payload = {
                    error: error?.response?.data?.message || error?.message || 'Failed to load DataMixer lake'
                }
                this.error = payload.error
                this.setDetail('Lake Load Error', payload)
            } finally {
                this.lakeActionRunning = false
            }
        },
        async unloadActiveLake() {
            if (this.lakeActionRunning) return
            this.lakeActionRunning = true
            this.error = ''
            try {
                const res = await deleteDataMixerLake({
                    link: this.lakePath,
                    delete_warehouse: false,
                    yes: false
                })
                if (res?.code && res.code !== 200) {
                    this.error = res.message || 'Failed to unload DataMixer lake'
                    this.setDetail('Lake Delete Error', res)
                    return
                }
                this.setDetail('Lake Delete Result', res.data)
                this.monitor = null
                if (this.lakeScan?.lakes) {
                    this.lakeScan.lakes = this.lakeScan.lakes.map((item) => ({
                        ...item,
                        active: false
                    }))
                }
                this.lakeState = {
                    status: 'missing',
                    warehouse: '',
                    warehouse_exists: false
                }
                this.pendingWorkbenchRefresh = true
            } catch (error) {
                const payload = {
                    error: error?.response?.data?.message || error?.message || 'Failed to unload DataMixer lake'
                }
                this.error = payload.error
                this.setDetail('Lake Delete Error', payload)
            } finally {
                this.lakeActionRunning = false
            }
        },
        async probeEmbedding() {
            if (this.probing || !this.monitor) return
            this.probing = true
            this.error = ''
            try {
                const res = await getDataMixerEmbeddingHealth({ lake: this.lakePath, timeout_seconds: 3 })
                if (res?.code && res.code !== 200) {
                    this.error = res.message || 'Embedding probe failed'
                    return
                }
                if (this.monitor?.embedding) {
                    this.monitor.embedding.probe = res.data
                }
                this.setDetail('Embedding Probe', res.data)
            } catch (error) {
                this.error = error?.response?.data?.message || error?.message || 'Embedding probe failed'
            } finally {
                this.probing = false
            }
        },
        setDetail(title, payload) {
            this.detail = { title, payload }
        },
        applyCommandTemplate(command) {
            if (!command) return
            this.commandLine = command
        },
        async runCommand() {
            if (this.commandRunning) return
            const line = String(this.commandLine || '').trim()
            if (!line) return
            this.commandRunning = true
            this.error = ''
            try {
                const res = await runDataMixerCli({
                    lake: this.lakePath,
                    line
                })
                if (res?.code && res.code !== 200) {
                    this.error = res.message || 'DataMixer command failed'
                    this.commandResult = res
                    this.setDetail('DataMixer Command Error', res)
                    return
                }
                this.commandResult = res.data
                this.setDetail('DataMixer Command Result', res.data)
                if (res.data?.exit === 0) {
                    await this.refresh()
                } else {
                    this.error =
                        res.data?.output?.error ||
                        res.data?.raw ||
                        `DataMixer command exited with code ${res.data?.exit ?? 'unknown'}`
                }
            } catch (error) {
                const payload = {
                    error: error?.response?.data?.message || error?.message || 'DataMixer command failed'
                }
                this.commandResult = payload
                this.setDetail('DataMixer Command Error', payload)
            } finally {
                this.commandRunning = false
            }
        },
        severityColor(severity) {
            const key = String(severity).toLowerCase()
            if (key.includes('error') || key.includes('critical')) return 'rgba(185, 72, 83, 0.78)'
            if (key.includes('warn')) return 'rgba(203, 101, 36, 0.78)'
            return 'rgba(64, 99, 170, 0.72)'
        },
        formatNumber(value) {
            return Number(value || 0).toLocaleString()
        },
        formatPercent(value) {
            return `${Math.round(Number(value || 0) * 100)}%`
        },
        formatBytes(value) {
            const bytes = Number(value || 0)
            if (bytes < 1024) return `${bytes} B`
            if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
            return `${(bytes / 1024 / 1024).toFixed(1)} MB`
        },
        formatTime(value) {
            if (!value) return '-'
            return String(value).replace('T', ' ').slice(0, 19)
        },
        shortPath(value) {
            const text = String(value || '')
            if (!text) return '-'
            const parts = text.split('/').filter(Boolean)
            if (parts.length <= 3) return text
            return `.../${parts.slice(-3).join('/')}`
        },
        formatCell(value) {
            if (value === undefined || value === null || value === '') return '-'
            if (typeof value === 'object') return JSON.stringify(value)
            const text = String(value)
            return text.length > 120 ? `${text.slice(0, 119)}...` : text
        }
    }
}
</script>

<style lang="scss" scoped>
.dm-container {
    position: relative;
    width: 100%;
    height: 100%;
    background-color: var(--lp-surface);
    display: flex;
    justify-content: center;

    .dm-major-container {
        position: relative;
        width: 100%;
        max-width: 1540px;
        height: 100%;
        min-height: 0;
        display: flex;
        flex-direction: column;
    }

    .dm-title-block {
        @include HbetweenVcenter;

        position: absolute;
        width: 100%;
        padding: 22px 18px 12px;
        z-index: 2;
        gap: 16px;
        background: var(--lp-surface);
        backdrop-filter: blur(18px);

        .title-copy {
            min-width: 240px;
            overflow: hidden;
        }

        .main-title {
            font-size: 26px;
            font-weight: 600;
            color: var(--lp-text);
            line-height: 1.2;
        }

        .sub-title {
            @include nowrap;

            max-width: min(780px, 52vw);
            margin-top: 5px;
            font-size: 13px;
            color: var(--lp-text);
        }

        .view-switch {
            @include Vcenter;

            gap: 6px;
            margin-top: 10px;

            button {
                height: 30px;
                padding: 0 10px;
                border: 1px solid rgba(48, 55, 67, 0.1);
                border-radius: 6px;
                color: var(--lp-text);
                background: var(--lp-surface);
                cursor: pointer;
                font-size: 12px;

                &.active {
                    color: white;
                    border-color: rgba(64, 99, 170, 1);
                    background: rgba(64, 99, 170, 1);
                }
            }
        }

        .right-block {
            @include HendVcenter;

            flex: 1;
            gap: 8px;
            min-width: 360px;

            .lake-input {
                width: min(380px, 32vw);
                min-width: 220px;
            }
        }
    }

    .dm-content-block {
        @include narrow-scroll-bar;

        position: relative;
        width: 100%;
        height: 100%;
        min-height: 0;
        padding: 122px 18px 18px;
        overflow: overlay;
    }
}

.error-banner,
.lake-runtime-panel,
.surface-panel,
.dm-panel,
.kpi-card {
    border: 1px solid rgba(42, 47, 56, 0.08);
    border-radius: 8px;
    background: var(--lp-surface);
    box-shadow: 0 12px 26px rgba(28, 32, 40, 0.055);
}

.error-banner {
    @include Vcenter;

    gap: 8px;
    min-height: 44px;
    margin-bottom: 10px;
    padding: 0 14px;
    color: rgba(157, 48, 58, 1);
    background: var(--lp-surface);
}

.monitor-loading-strip {
    display: grid;
    grid-template-columns: minmax(180px, auto) minmax(160px, 1fr);
    align-items: center;
    gap: 12px;
    min-height: 34px;
    margin-bottom: 10px;
    padding: 0 12px;
    border-radius: 8px;
    color: rgba(64, 99, 170, 1);
    background: rgba(64, 99, 170, 0.1);
    font-size: 12px;

    .monitor-loading-copy {
        @include Vcenter;

        gap: 8px;
        min-width: 0;

        span {
            @include nowrap;
        }
    }

    .monitor-progress {
        position: relative;
        height: 5px;
        min-width: 120px;
        overflow: hidden;
        border-radius: 999px;
        background: rgba(64, 99, 170, 0.14);

        span {
            position: absolute;
            top: 0;
            left: -35%;
            width: 35%;
            height: 100%;
            border-radius: inherit;
            background: rgba(64, 99, 170, 0.95);
            animation: monitor-progress 1.2s ease-in-out infinite;
        }
    }
}

@keyframes monitor-progress {
    0% {
        left: -35%;
    }

    100% {
        left: 100%;
    }
}

.lake-runtime-panel {
    display: grid;
    grid-template-columns: minmax(260px, 0.9fr) minmax(420px, 1.45fr) auto;
    align-items: stretch;
    gap: 12px;
    min-height: 112px;
    padding: 12px;

    &.good {
        border-color: rgba(20, 145, 116, 0.2);
    }

    &.running {
        border-color: rgba(64, 99, 170, 0.26);
    }

    &.warn {
        border-color: rgba(203, 101, 36, 0.3);
    }

    &.bad {
        border-color: rgba(185, 72, 83, 0.3);
    }

    &.missing {
        border-color: rgba(104, 121, 141, 0.22);
    }

    .runtime-primary {
        @include Vcenter;

        gap: 12px;
        min-width: 0;
    }

    .runtime-icon {
        @include HcenterVcenter;

        width: 52px;
        height: 52px;
        flex-shrink: 0;
        border-radius: 8px;
        color: white;
        background: rgba(64, 99, 170, 1);
        font-size: 20px;
    }

    &.good .runtime-icon {
        background: rgba(20, 145, 116, 1);
    }

    &.warn .runtime-icon {
        background: rgba(203, 101, 36, 1);
    }

    &.bad .runtime-icon {
        background: rgba(185, 72, 83, 1);
    }

    &.missing .runtime-icon {
        background: rgba(104, 121, 141, 1);
    }

    .runtime-copy {
        min-width: 0;
    }

    .runtime-title {
        color: var(--lp-text);
        font-size: 17px;
        font-weight: 700;
    }

    .runtime-subtitle {
        @include nowrap;

        margin-top: 7px;
        color: var(--lp-text);
        font-size: 12px;
    }

    .runtime-states {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(128px, 1fr));
        gap: 8px;
        min-width: 0;
    }

    .runtime-state {
        min-width: 0;
        min-height: 84px;
        padding: 10px;
        border: 1px solid rgba(48, 55, 67, 0.08);
        border-radius: 6px;
        background: var(--lp-surface);
        cursor: pointer;
        text-align: left;

        span,
        strong,
        small {
            @include nowrap;

            display: block;
        }

        span {
            color: var(--lp-text);
            font-size: 11px;
        }

        strong {
            margin-top: 7px;
            color: var(--lp-text);
            font-size: 15px;
            font-weight: 700;
        }

        small {
            margin-top: 7px;
            color: rgba(105, 111, 123, 1);
            font-size: 11px;
        }

        &.good {
            border-color: rgba(20, 145, 116, 0.18);
            background: var(--lp-surface);
        }

        &.running {
            border-color: rgba(64, 99, 170, 0.22);
            background: var(--lp-surface);
        }

        &.warn {
            border-color: rgba(203, 101, 36, 0.22);
            background: var(--lp-surface);
        }

        &.bad {
            border-color: rgba(185, 72, 83, 0.24);
            background: var(--lp-surface);
        }

        &.missing {
            background: var(--lp-surface);
        }
    }

    .runtime-actions {
        display: grid;
        align-content: center;
        gap: 8px;
        min-width: 132px;

        button {
            @include HcenterVcenter;

            gap: 6px;
            min-height: 34px;
            padding: 0 12px;
            border: none;
            border-radius: 6px;
            color: white;
            background: rgba(64, 99, 170, 1);
            cursor: pointer;
            white-space: nowrap;

            &:disabled {
                opacity: 0.55;
                cursor: default;
            }

            &.secondary {
                color: rgba(64, 99, 170, 1);
                background: rgba(64, 99, 170, 0.1);
            }

            &.attention {
                background: rgba(203, 101, 36, 1);
            }
        }
    }
}

.surface-panel {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 8px;
    margin-top: 10px;
    padding: 10px;

    .surface-item {
        @include Vcenter;

        gap: 8px;
        min-height: 44px;
        min-width: 0;
        padding: 0 10px;
        border-radius: 6px;
        border: none;
        background: var(--lp-surface);
        cursor: pointer;
        text-align: left;

        &:hover {
            background: var(--lp-surface);
        }

        .surface-dot {
            width: 9px;
            height: 9px;
            flex-shrink: 0;
            border-radius: 50%;
            background: rgba(20, 145, 116, 1);
        }

        &.missing .surface-dot {
            background: rgba(203, 101, 36, 1);
        }

        div {
            min-width: 0;
        }

        p,
        span {
            @include nowrap;
        }

        p {
            color: var(--lp-text);
            font-size: 13px;
            font-weight: 600;
        }

        span {
            display: block;
            margin-top: 3px;
            color: var(--lp-text);
            font-size: 12px;
        }
    }
}

.command-panel {
    min-height: 280px;
    margin-top: 10px;

    .command-layout {
        display: grid;
        grid-template-columns: minmax(0, 1fr) minmax(360px, 0.75fr);
        gap: 12px;
        flex: 1;
        min-height: 0;
    }

    .command-groups {
        @include narrow-scroll-bar;

        display: grid;
        align-content: start;
        gap: 10px;
        min-height: 0;
        overflow: overlay;
    }

    .command-group {
        padding: 10px;
        border-radius: 6px;
        background: var(--lp-surface);

        p {
            margin-bottom: 8px;
            color: var(--lp-text);
            font-size: 13px;
            font-weight: 600;
        }
    }

    .command-chips {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;

        button {
            min-height: 28px;
            padding: 0 9px;
            border: 1px solid rgba(48, 55, 67, 0.1);
            border-radius: 6px;
            background: var(--lp-surface);
            color: var(--lp-text);
            font-size: 12px;
            cursor: pointer;

            &:hover {
                color: rgba(64, 99, 170, 1);
                border-color: rgba(64, 99, 170, 0.28);
            }
        }
    }

    .command-runner {
        display: grid;
        grid-template-rows: 86px auto minmax(0, 1fr);
        gap: 8px;
        min-height: 0;

        textarea,
        pre {
            @include narrow-scroll-bar;

            width: 100%;
            border: 1px solid rgba(48, 55, 67, 0.1);
            border-radius: 6px;
            font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
            font-size: 12px;
            line-height: 1.5;
            resize: none;
        }

        textarea {
            padding: 9px;
            background: var(--lp-surface);
            color: var(--lp-text);
            outline: none;

            &:focus {
                border-color: rgba(64, 99, 170, 0.48);
            }
        }

        pre {
            min-height: 0;
            margin: 0;
            padding: 10px;
            overflow: overlay;
            color: rgba(235, 239, 246, 1);
            background: rgba(33, 37, 46, 1);
            white-space: pre-wrap;
            word-break: break-word;
        }
    }

    .command-actions {
        @include Vcenter;

        gap: 7px;
        flex-wrap: wrap;

        button {
            @include HcenterVcenter;

            gap: 6px;
            min-height: 32px;
            padding: 0 12px;
            border: none;
            border-radius: 6px;
            background: rgba(64, 99, 170, 1);
            color: white;
            cursor: pointer;

            &:disabled {
                cursor: default;
                opacity: 0.55;
            }

            &.secondary {
                color: rgba(64, 99, 170, 1);
                background: rgba(64, 99, 170, 0.1);
            }
        }
    }
}

.lake-dashboard {
    display: grid;
    grid-template-columns: 360px minmax(0, 1fr);
    gap: 10px;
    align-items: stretch;

    .active-lake-panel,
    .lake-candidates-panel {
        min-height: 320px;
    }

    .active-lake-grid {
        display: grid;
        grid-template-columns: 96px minmax(0, 1fr);
        gap: 12px;
        align-items: center;
        min-height: 120px;
    }

    .active-score {
        @include HcenterVcenterC;

        width: 96px;
        height: 96px;
        border-radius: 8px;
        color: white;
        background: rgba(64, 99, 170, 1);

        span {
            max-width: 86px;
            overflow: hidden;
            text-overflow: ellipsis;
            font-size: 23px;
            font-weight: 700;
            line-height: 1.1;
        }

        p {
            margin-top: 5px;
            font-size: 12px;
        }
    }

    .active-meta {
        min-width: 0;

        p,
        span {
            @include nowrap;
        }

        p {
            color: var(--lp-text);
            font-size: 14px;
            font-weight: 600;
        }

        span {
            display: block;
            margin-top: 8px;
            color: var(--lp-text);
            font-size: 12px;
        }
    }

    .lake-card-grid {
        @include narrow-scroll-bar;

        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
        gap: 10px;
        flex: 1;
        min-height: 0;
        overflow: overlay;
    }

    .lake-card {
        display: grid;
        gap: 10px;
        min-width: 0;
        min-height: 164px;
        padding: 12px;
        border: 1px solid rgba(48, 55, 67, 0.1);
        border-radius: 8px;
        background: var(--lp-surface);
        cursor: pointer;

        &.active {
            border-color: rgba(20, 145, 116, 0.42);
            background: var(--lp-surface);
        }

        &.missing {
            border-color: rgba(185, 72, 83, 0.28);
            background: var(--lp-surface);
        }
    }

    .lake-card-head {
        @include HbetweenVcenter;

        min-width: 0;
        gap: 8px;

        div {
            min-width: 0;
        }

        p,
        span {
            @include nowrap;
        }

        p {
            color: var(--lp-text);
            font-size: 14px;
            font-weight: 700;
        }

        span {
            display: block;
            margin-top: 4px;
            color: var(--lp-text);
            font-size: 12px;
        }

        strong {
            flex-shrink: 0;
            padding: 4px 8px;
            border-radius: 999px;
            color: rgba(20, 120, 96, 1);
            background: rgba(20, 145, 116, 0.12);
            font-size: 12px;
            font-weight: 600;
        }
    }

    .lake-card-metrics {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 6px;

        span {
            @include nowrap;

            padding: 7px 8px;
            border-radius: 6px;
            color: var(--lp-text);
            background: var(--lp-surface);
            font-size: 12px;
            text-align: center;
        }
    }

    .lake-path {
        @include nowrap;

        color: var(--lp-text);
        font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
        font-size: 12px;
    }

    .lake-card-actions,
    .lake-action-row {
        @include Vcenter;

        gap: 8px;
        flex-wrap: wrap;

        button {
            @include HcenterVcenter;

            gap: 6px;
            min-height: 32px;
            padding: 0 11px;
            border: none;
            border-radius: 6px;
            color: white;
            background: rgba(64, 99, 170, 1);
            cursor: pointer;

            &:disabled {
                opacity: 0.55;
                cursor: default;
            }

            &.secondary {
                color: rgba(64, 99, 170, 1);
                background: rgba(64, 99, 170, 0.1);
            }
        }
    }

    .empty-lakes {
        @include HcenterVcenter;

        min-height: 140px;
        border-radius: 8px;
        color: var(--lp-text);
        background: var(--lp-surface);
        font-size: 13px;
    }
}

.kpi-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
    gap: 10px;
    margin-top: 10px;

    .kpi-card {
        @include Vcenter;

        min-height: 96px;
        padding: 12px;
        gap: 12px;
        cursor: pointer;
        text-align: left;
        transition:
            transform 0.18s ease,
            box-shadow 0.18s ease;

        &:hover {
            transform: translateY(-1px);
            box-shadow: 0 14px 28px rgba(28, 32, 40, 0.08);
        }

        .kpi-icon {
            @include HcenterVcenter;

            width: 44px;
            height: 44px;
            border-radius: 8px;
            flex-shrink: 0;
            font-size: 18px;
        }

        .kpi-copy {
            min-width: 0;
        }

        .kpi-label {
            font-size: 12px;
            color: var(--lp-text);
        }

        .kpi-value {
            margin-top: 4px;
            font-size: 24px;
            font-weight: 700;
            color: var(--lp-text);
            line-height: 1.1;
        }

        .kpi-note {
            @include nowrap;

            margin-top: 5px;
            font-size: 12px;
            color: rgba(105, 111, 123, 1);
        }
    }
}

.benchmark-panel {
    min-height: 148px;
    margin-top: 10px;

    .benchmark-layout {
        display: grid;
        grid-template-columns: 220px minmax(0, 1fr);
        gap: 12px;
        min-height: 86px;
    }

    .benchmark-score {
        display: flex;
        flex-direction: column;
        justify-content: center;
        min-width: 0;
        padding: 14px;
        border: 1px solid rgba(185, 72, 83, 0.14);
        border-radius: 6px;
        background: var(--lp-surface);

        strong {
            font-size: 30px;
            line-height: 1;
            color: rgba(154, 52, 62, 1);
        }

        span {
            @include nowrap;

            margin-top: 8px;
            font-size: 12px;
            color: var(--lp-text);
        }
    }

    .benchmark-table {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 8px;
        align-content: start;
        min-width: 0;
    }

    .benchmark-item {
        display: grid;
        grid-template-columns: minmax(0, 1fr) 82px;
        align-items: center;
        gap: 8px;
        min-height: 38px;
        padding: 8px 10px;
        border: 1px solid rgba(48, 55, 67, 0.08);
        border-radius: 6px;
        background: var(--lp-surface);
        cursor: pointer;

        span {
            @include nowrap;

            font-size: 12px;
            color: var(--lp-text);
        }

        strong {
            text-align: right;
            font-size: 12px;
            color: rgba(154, 52, 62, 1);
        }
    }

    .benchmark-empty {
        display: flex;
        align-items: center;
        min-height: 38px;
        padding: 8px 10px;
        border: 1px dashed rgba(104, 121, 141, 0.24);
        border-radius: 6px;
        color: rgba(105, 111, 123, 1);
        font-size: 12px;
    }
}

.main-grid,
.insight-grid,
.detail-grid {
    display: grid;
    gap: 10px;
    margin-top: 10px;
}

.main-grid {
    grid-template-columns: minmax(0, 1fr) minmax(420px, 0.9fr);
}

.insight-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));

    .dm-panel {
        height: 340px;
        min-height: 340px;
    }

    :deep(.ol-chart-shell) {
        height: 100%;
        max-height: 250px;
    }
}

.detail-grid {
    grid-template-columns: minmax(0, 1fr) 380px;

    &.simple {
        grid-template-columns: minmax(0, 1fr);
    }
}

.dm-panel {
    display: flex;
    flex-direction: column;
    min-width: 0;
    min-height: 250px;
    padding: 12px;
    overflow: hidden;

    .panel-head {
        @include HbetweenVcenter;

        min-height: 30px;
        gap: 12px;
        margin-bottom: 8px;
        flex-shrink: 0;

        p {
            @include nowrap;

            font-size: 15px;
            font-weight: 600;
            color: var(--lp-text);
        }

        span {
            @include nowrap;

            font-size: 12px;
            color: var(--lp-text);
        }
    }

    :deep(.ol-chart-shell) {
        flex: 1;
        min-height: 0;
    }
}

.warehouse-panel {
    min-height: 300px;

    .warehouse-layout {
        display: grid;
        grid-template-columns: 230px minmax(0, 1fr);
        gap: 10px;
        flex: 1;
        min-height: 0;
    }

    .config-grid {
        display: grid;
        align-content: start;
        gap: 8px;
    }

    .config-tile {
        min-width: 0;
        min-height: 48px;
        padding: 8px;
        border: none;
        border-radius: 6px;
        background: var(--lp-surface);
        text-align: left;
        cursor: pointer;

        span {
            display: block;
            font-size: 11px;
            color: var(--lp-text);
        }

        p {
            @include nowrap;

            margin-top: 5px;
            font-size: 12px;
            font-weight: 600;
            color: var(--lp-text);
        }
    }

    .table-health {
        @include narrow-scroll-bar;

        display: grid;
        align-content: start;
        gap: 6px;
        min-height: 0;
        overflow: overlay;
    }

    .table-row {
        display: grid;
        grid-template-columns: 18px minmax(130px, 1fr) 76px 82px 138px;
        align-items: center;
        gap: 8px;
        width: 100%;
        min-height: 36px;
        padding: 0 8px;
        border: none;
        border-radius: 6px;
        background: var(--lp-surface);
        color: var(--lp-text);
        font-size: 12px;
        text-align: left;
        cursor: pointer;

        &:hover {
            background: var(--lp-surface);
        }

        .table-status {
            width: 9px;
            height: 9px;
            border-radius: 50%;
            background: rgba(185, 72, 83, 1);

            &.ok {
                background: rgba(20, 145, 116, 1);
            }
        }

        .table-name,
        .table-time {
            @include nowrap;
        }

        .table-count,
        .table-size {
            text-align: right;
            font-variant-numeric: tabular-nums;
        }
    }
}

.segmented-control {
    @include Vcenter;

    gap: 5px;
    flex-wrap: wrap;

    button {
        height: 30px;
        padding: 0 10px;
        border: 1px solid rgba(48, 55, 67, 0.1);
        border-radius: 6px;
        background: var(--lp-surface);
        color: var(--lp-text);
        cursor: pointer;
        font-size: 12px;

        &.active {
            color: white;
            background: rgba(64, 99, 170, 1);
        }
    }
}

.embedding-panel {
    .embedding-layout {
        display: grid;
        grid-template-rows: minmax(0, 1fr) auto;
        gap: 8px;
        flex: 1;
        min-height: 0;
    }

    .embedding-meta {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 6px;

        p {
            @include nowrap;

            padding: 6px 8px;
            border-radius: 6px;
            background: var(--lp-surface);
            color: var(--lp-text);
            font-size: 12px;
        }

        span {
            margin-right: 6px;
            color: var(--lp-text);
        }
    }
}

.inspect-panel {
    min-height: 280px;

    pre {
        @include narrow-scroll-bar;

        width: 100%;
        flex: 1;
        min-height: 0;
        padding: 10px;
        border-radius: 6px;
        background: rgba(32, 35, 42, 1);
        color: rgba(239, 242, 247, 1);
        font-size: 12px;
        line-height: 1.5;
        overflow: overlay;
        white-space: pre-wrap;
        word-break: break-word;
    }
}

.latest-panel {
    min-height: 300px;
    margin-top: 10px;

    .preview-table {
        @include narrow-scroll-bar;

        width: 100%;
        flex: 1;
        min-height: 0;
        max-height: 420px;
        overflow: overlay;

        table {
            width: 100%;
            border-collapse: collapse;
            table-layout: fixed;
        }

        th,
        td {
            min-height: 38px;
            padding: 9px 8px;
            border-bottom: 1px solid rgba(48, 55, 67, 0.08);
            color: var(--lp-text);
            font-size: 12px;
            text-align: left;
            vertical-align: top;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }

        th {
            position: sticky;
            top: 0;
            z-index: 1;
            background: var(--lp-surface);
            color: var(--lp-text);
            font-weight: 600;
        }

        tbody tr {
            cursor: pointer;

            &:hover td {
                background: var(--lp-surface);
            }
        }
    }
}

@media screen and (max-width: 1280px) {
    .surface-panel,
    .kpi-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .lake-runtime-panel {
        grid-template-columns: minmax(0, 1fr);

        .runtime-actions {
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }
    }

    .main-grid,
    .detail-grid,
    .command-panel .command-layout,
    .lake-dashboard {
        grid-template-columns: minmax(0, 1fr);
    }

    .insight-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .benchmark-panel .benchmark-layout {
        grid-template-columns: minmax(0, 1fr);
    }
}

@media screen and (max-width: 820px) {
    .dm-container {
        .dm-title-block {
            align-items: stretch;
            flex-direction: column;

            .sub-title {
                max-width: 100%;
            }

            .right-block {
                justify-content: flex-start;
                min-width: 0;
                flex-wrap: wrap;

                .lake-input {
                    width: 100%;
                    min-width: 0;
                }
            }
        }

        .dm-content-block {
            padding-top: 198px;
        }
    }

    .surface-panel,
    .kpi-grid,
    .insight-grid {
        grid-template-columns: minmax(0, 1fr);
    }

    .command-panel {
        .command-layout {
            grid-template-columns: minmax(0, 1fr);
        }

        .command-chips {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(88px, 1fr));

            button {
                min-width: 0;
            }
        }

        .command-runner {
            grid-template-rows: 92px auto 220px;
        }
    }

    .lake-runtime-panel {
        grid-template-columns: minmax(0, 1fr);

        .runtime-states {
            grid-template-columns: minmax(0, 1fr);
        }

        .runtime-actions {
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }
    }

    .warehouse-panel {
        .warehouse-layout {
            grid-template-columns: minmax(0, 1fr);
        }

        .table-row {
            grid-template-columns: 18px minmax(120px, 1fr) 64px 76px;

            .table-time {
                display: none;
            }
        }
    }
}
</style>

<style lang="scss">
/* DataMixer keeps its layout; only its surface colours move onto the tokens. */
.dm-container {
    background: var(--lp-bg);
    color: var(--lp-text);

    .dm-major-container {
        max-width: none;
    }

    .dm-title-block {
        position: relative;
        padding: 0 12px 0 16px;
        background: transparent;
        backdrop-filter: none;

        .lake-input {
            width: 240px;
        }

        .dm-workspace {
            max-width: 320px;
            font-family: var(--lp-mono);
            font-size: var(--lp-t-sm);
            color: var(--lp-text-mute);
        }

        .is-on {
            background: var(--lp-accent-wash);
            color: var(--lp-accent);
        }
    }

    .dm-content-block {
        padding: 24px 28px 48px 28px;
        gap: 24px;
    }

    .dm-panel,
    .lake-card,
    .kpi-card,
    .config-tile,
    .runtime-state,
    .lake-runtime-panel,
    .surface-item,
    .benchmark-item {
        background: var(--lp-surface);
        border: 1px solid var(--lp-line);
        border-radius: var(--lp-r-3);
        box-shadow: none;
        color: var(--lp-text);
    }

    .lake-card.active,
    .runtime-state.active {
        border-color: var(--lp-accent-line);
    }

    .lake-card.missing {
        opacity: 0.6;
    }

    .panel-head {
        p {
            font-family: var(--lp-mono);
            font-size: var(--lp-t-sm);
            letter-spacing: 0.09em;
            text-transform: uppercase;
            color: var(--lp-text-mute);
        }

        span {
            font-family: var(--lp-mono);
            font-size: var(--lp-t-sm);
            color: var(--lp-text-faint);
        }
    }

    .active-score span,
    .kpi-value,
    .benchmark-score {
        font-family: var(--lp-mono);
        font-weight: 500;
        color: var(--lp-text);
    }

    .kpi-label,
    .kpi-note,
    .runtime-subtitle,
    .lake-path,
    .active-meta span,
    .lake-card-metrics span {
        font-family: var(--lp-mono);
        font-size: var(--lp-t-sm);
        color: var(--lp-text-mute);
    }

    .empty-lakes,
    .benchmark-empty {
        color: var(--lp-text-faint);
    }

    button {
        color: inherit;
    }

    .lake-action-row button,
    .lake-card-actions button,
    .runtime-actions button,
    .command-actions button,
    .command-chips button {
        height: var(--lp-h-md);
        padding: 0 10px;
        gap: 6px;
        background: var(--lp-raised);
        color: var(--lp-text);
        border: 1px solid var(--lp-line-hi);
        border-radius: var(--lp-r-2);
        font-size: var(--lp-t-cap);
        display: inline-flex;
        align-items: center;

        &:hover {
            background: var(--lp-active);
        }

        &.secondary {
            background: transparent;
            color: var(--lp-text-dim);
            border-color: var(--lp-line);
        }

        &:disabled {
            background: var(--lp-surface);
            color: var(--lp-text-faint);
            cursor: not-allowed;
        }
    }

    .command-runner textarea,
    .preview-table {
        background: var(--lp-bg);
        color: var(--lp-text);
        border: 1px solid var(--lp-line-hi);
        border-radius: var(--lp-r-2);
        font-family: var(--lp-mono);
        font-size: var(--lp-t-cap);
    }

    .monitor-progress {
        background: var(--lp-line);

        span {
            background: var(--lp-accent);
        }
    }

    .monitor-loading-copy,
    .error-banner {
        font-family: var(--lp-mono);
        font-size: var(--lp-t-cap);
    }

    .error-banner {
        padding: 8px 12px;
        gap: 8px;
        background: var(--lp-err-wash);
        color: var(--lp-err);
        border: 1px solid var(--lp-err-line);
        border-radius: var(--lp-r-2);
    }
}
</style>
