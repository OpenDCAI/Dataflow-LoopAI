<template>
    <div class="ol-monitor-container">
        <div class="ol-major-container">
            <div class="ol-title-block">
                <div class="title-copy">
                    <p class="main-title">Obtainer Lake Monitor</p>
                    <p class="sub-title" :title="lakeRoot || lakePath">
                        {{ lakeRoot || lakePath }}
                    </p>
                </div>
                <div class="right-block">
                    <fv-text-box
                        v-model="lakePath"
                        class="lake-input"
                        border-radius="6"
                        :reveal-border="true"
                        :is-box-shadow="true"
                    ></fv-text-box>
                    <fv-button
                        icon="Refresh"
                        :disabled="loading"
                        border-radius="6"
                        :is-box-shadow="true"
                        @click="refresh"
                    >
                        {{ local('Refresh') }}
                    </fv-button>
                    <fv-button
                        theme="dark"
                        icon="LightningBolt"
                        :disabled="loading || probing"
                        :background="gradient"
                        border-radius="6"
                        :is-box-shadow="true"
                        @click="probeEmbedding"
                    >
                        {{ probing ? local('Probing') : local('Probe') }}
                    </fv-button>
                </div>
            </div>

            <div class="ol-content-block">
                <page-loading :model-value="loading" title="Loading..." :z-index="3"></page-loading>

                <div v-if="error" class="error-banner">
                    <i class="ms-Icon ms-Icon--ErrorBadge"></i>
                    <span>{{ error }}</span>
                </div>

                <div class="status-strip">
                    <div class="health-score" :class="healthLevel">
                        <span>{{ summary.health_score ?? 0 }}</span>
                        <p>Health</p>
                    </div>
                    <div class="status-meta">
                        <p class="status-title">{{ monitorStatus }}</p>
                        <p class="status-subtitle">
                            {{ local('Last refresh') }}: {{ monitor?.refreshed_at || '-' }}
                        </p>
                    </div>
                    <div class="warning-pill" :class="{ active: warnings.length > 0 }">
                        <i class="ms-Icon ms-Icon--Warning"></i>
                        <span>{{ warnings.length }}</span>
                    </div>
                </div>

                <div class="kpi-grid">
                    <button
                        v-for="item in kpis"
                        :key="item.key"
                        class="kpi-card"
                        type="button"
                        @click="setDetail(item.label, item)"
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

                <div class="chart-grid primary">
                    <section class="monitor-panel trend-panel">
                        <div class="panel-head">
                            <p>Ingest Trend</p>
                            <span>{{ ingestRows.length }} runs</span>
                        </div>
                        <base-chart type="bar" :chart-data="ingestTrendChart" :options="trendOptions"></base-chart>
                    </section>
                    <section class="monitor-panel composition-panel">
                        <div class="panel-head">
                            <p>Lake Composition</p>
                            <span>processing level</span>
                        </div>
                        <base-chart
                            type="doughnut"
                            :chart-data="compositionChart"
                            :options="doughnutOptions"
                        ></base-chart>
                    </section>
                </div>

                <div class="chart-grid secondary">
                    <section class="monitor-panel">
                        <div class="panel-head">
                            <p>Top Tags</p>
                            <span>{{ topTags.length }} values</span>
                        </div>
                        <base-chart type="bar" :chart-data="tagChart" :options="horizontalBarOptions"></base-chart>
                    </section>
                    <section class="monitor-panel">
                        <div class="panel-head">
                            <p>Quality Findings</p>
                            <span>{{ summary.quality_findings || 0 }} items</span>
                        </div>
                        <base-chart type="bar" :chart-data="qualityChart" :options="stackedOptions"></base-chart>
                    </section>
                    <section class="monitor-panel">
                        <div class="panel-head">
                            <p>Embedding Health</p>
                            <span>{{ formatPercent(embedding.coverage) }}</span>
                        </div>
                        <div class="embedding-block">
                            <base-chart
                                type="doughnut"
                                :chart-data="embeddingChart"
                                :options="doughnutOptions"
                            ></base-chart>
                            <div class="embedding-meta">
                                <p><span>Model</span>{{ embedding.embedding_model || '-' }}</p>
                                <p><span>Backend</span>{{ embedding.embedding_backend || '-' }}</p>
                                <p><span>Provider</span>{{ embedding.embedding_provider || '-' }}</p>
                                <p><span>Probe</span>{{ embedding.probe?.status || 'not_checked' }}</p>
                            </div>
                        </div>
                    </section>
                </div>

                <div class="deep-grid">
                    <section class="monitor-panel table-panel">
                        <div class="panel-head">
                            <p>Table Health</p>
                            <span>{{ tableRows.length }} tables</span>
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
                    </section>

                    <aside class="monitor-panel detail-panel">
                        <div class="panel-head">
                            <p>{{ detailTitle }}</p>
                            <span>Drill-down</span>
                        </div>
                        <pre>{{ detailText }}</pre>
                    </aside>
                </div>

                <section class="monitor-panel latest-panel">
                    <div class="panel-head">
                        <p>Latest</p>
                        <div class="tab-controls">
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
                                    :key="index"
                                    @click="setDetail(activeTab, row)"
                                >
                                    <td v-for="column in activeColumns" :key="column.key">
                                        {{ formatCell(row[column.key]) }}
                                    </td>
                                </tr>
                                <tr v-if="activeRows.length === 0">
                                    <td :colspan="activeColumns.length">{{ local('No Data') }}</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </section>
            </div>
        </div>
    </div>
</template>

<script>
import { mapState } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'
import { useTheme } from '@/stores/theme'
import pageLoading from '@/components/general/pageLoading.vue'
import baseChart from '@/components/manage/obtainerLake/baseChart.vue'
import { getEmbeddingHealth, getLakeMonitor } from '@/services/obtainerLake'

export default {
    components: {
        pageLoading,
        baseChart
    },
    data() {
        return {
            lakePath: '.loopai/lake.yaml',
            monitor: null,
            loading: false,
            probing: false,
            error: '',
            activeTab: 'records',
            detail: null
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        ...mapState(useTheme, ['gradient']),
        summary() {
            return this.monitor?.summary || {}
        },
        embedding() {
            return this.monitor?.embedding || { probe: { status: 'not_checked' } }
        },
        warnings() {
            return this.monitor?.warnings || []
        },
        lakeRoot() {
            return this.monitor?.lake_root || ''
        },
        monitorStatus() {
            if (!this.monitor) return 'No lake loaded'
            if (this.warnings.length > 0) return 'Warnings detected'
            return 'Healthy'
        },
        healthLevel() {
            const score = Number(this.summary.health_score || 0)
            if (score >= 85) return 'good'
            if (score >= 60) return 'warn'
            return 'bad'
        },
        kpis() {
            return [
                {
                    key: 'records',
                    label: 'Records',
                    value: this.formatNumber(this.summary.records || 0),
                    note: `${this.formatNumber(this.summary.datasets || 0)} datasets`,
                    icon: 'Database',
                    background: 'rgba(75, 111, 193, 0.12)',
                    color: 'rgba(75, 111, 193, 1)'
                },
                {
                    key: 'coverage',
                    label: 'Embedding Coverage',
                    value: this.formatPercent(this.summary.embedding_coverage || 0),
                    note: `${this.formatNumber(this.embedding.pending_records || 0)} pending`,
                    icon: 'LightningBolt',
                    background: 'rgba(20, 155, 124, 0.12)',
                    color: 'rgba(20, 120, 96, 1)'
                },
                {
                    key: 'quality',
                    label: 'Quality Findings',
                    value: this.formatNumber(this.summary.quality_findings || 0),
                    note: `${this.warnings.length} warnings`,
                    icon: 'Diagnostic',
                    background: 'rgba(209, 105, 40, 0.14)',
                    color: 'rgba(181, 83, 20, 1)'
                },
                {
                    key: 'exports',
                    label: 'Exports',
                    value: this.formatNumber(this.summary.exports || 0),
                    note: `${this.formatNumber(this.summary.ingest_runs || 0)} ingest runs`,
                    icon: 'Upload',
                    background: 'rgba(166, 80, 151, 0.12)',
                    color: 'rgba(136, 55, 121, 1)'
                }
            ]
        },
        ingestRows() {
            return this.monitor?.charts?.ingest_trend || []
        },
        topTags() {
            return this.monitor?.charts?.top_tags || []
        },
        tableRows() {
            return Object.values(this.monitor?.tables || {})
        },
        palette() {
            return [
                'rgba(75, 111, 193, 0.82)',
                'rgba(20, 155, 124, 0.82)',
                'rgba(209, 105, 40, 0.82)',
                'rgba(166, 80, 151, 0.82)',
                'rgba(224, 172, 62, 0.86)',
                'rgba(78, 151, 168, 0.82)',
                'rgba(190, 76, 88, 0.82)',
                'rgba(112, 127, 143, 0.82)'
            ]
        },
        ingestTrendChart() {
            const labels = this.ingestRows.map((row) => row.label || row.finished_at || '-')
            return {
                labels,
                datasets: [
                    {
                        label: 'Seen',
                        data: this.ingestRows.map((row) => row.rows_seen || 0),
                        backgroundColor: 'rgba(112, 127, 143, 0.5)'
                    },
                    {
                        label: 'Written',
                        data: this.ingestRows.map((row) => row.rows_written || 0),
                        backgroundColor: 'rgba(20, 155, 124, 0.72)'
                    },
                    {
                        label: 'Quarantined',
                        data: this.ingestRows.map((row) => row.rows_quarantined || 0),
                        backgroundColor: 'rgba(190, 76, 88, 0.72)'
                    }
                ]
            }
        },
        compositionChart() {
            const data = this.monitor?.charts?.composition?.processing_level || {}
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
                        label: 'Records',
                        data: rows.map((row) => row.count),
                        backgroundColor: 'rgba(75, 111, 193, 0.72)',
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
                labels: ['Indexed', 'Pending'],
                datasets: [
                    {
                        data: [this.embedding.indexed_records || 0, this.embedding.pending_records || 0],
                        backgroundColor: ['rgba(20, 155, 124, 0.78)', 'rgba(209, 105, 40, 0.45)'],
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
                { key: 'records', label: 'Records' },
                { key: 'ingest_runs', label: 'Ingest Runs' },
                { key: 'quality_findings', label: 'Quality' },
                { key: 'exports', label: 'Exports' },
                { key: 'warnings', label: 'Warnings' }
            ]
        },
        columns() {
            return {
                records: [
                    { key: 'record_id', label: 'Record' },
                    { key: 'domain', label: 'Domain' },
                    { key: 'processing_level', label: 'Level' },
                    { key: 'source_kind', label: 'Source' },
                    { key: 'text', label: 'Text' }
                ],
                ingest_runs: [
                    { key: 'ingest_run_id', label: 'Run' },
                    { key: 'status', label: 'Status' },
                    { key: 'rows_seen', label: 'Seen' },
                    { key: 'rows_written', label: 'Written' },
                    { key: 'finished_at', label: 'Finished' }
                ],
                quality_findings: [
                    { key: 'finding_type', label: 'Type' },
                    { key: 'severity', label: 'Severity' },
                    { key: 'score', label: 'Score' },
                    { key: 'detector', label: 'Detector' },
                    { key: 'created_at', label: 'Created' }
                ],
                exports: [
                    { key: 'export_id', label: 'Export' },
                    { key: 'strategy', label: 'Strategy' },
                    { key: 'actual_size', label: 'Rows' },
                    { key: 'format', label: 'Format' },
                    { key: 'created_at', label: 'Created' }
                ],
                warnings: [
                    { key: 'code', label: 'Code' },
                    { key: 'table', label: 'Table' },
                    { key: 'line', label: 'Line' },
                    { key: 'message', label: 'Message' }
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
            return this.detail?.title || 'Lake Summary'
        },
        detailText() {
            return JSON.stringify(this.detail?.payload || this.summary, null, 2)
        }
    },
    mounted() {
        this.refresh()
    },
    methods: {
        async refresh() {
            if (this.loading) return
            this.loading = true
            this.error = ''
            try {
                const res = await getLakeMonitor({ lake: this.lakePath })
                if (res.code !== 200) {
                    this.error = res.message || 'Failed to load lake monitor'
                    return
                }
                this.monitor = res.data
                this.detail = { title: 'Lake Summary', payload: res.data.summary }
            } catch (error) {
                this.error = error?.message || 'Failed to load lake monitor'
            } finally {
                this.loading = false
            }
        },
        async probeEmbedding() {
            if (this.probing) return
            this.probing = true
            this.error = ''
            try {
                const res = await getEmbeddingHealth({ lake: this.lakePath, timeout_seconds: 3 })
                if (res.code !== 200) {
                    this.error = res.message || 'Embedding probe failed'
                    return
                }
                if (this.monitor?.embedding) {
                    this.monitor.embedding.probe = res.data
                }
                this.setDetail('Embedding Probe', res.data)
            } catch (error) {
                this.error = error?.message || 'Embedding probe failed'
            } finally {
                this.probing = false
            }
        },
        setDetail(title, payload) {
            this.detail = { title, payload }
        },
        severityColor(severity) {
            const key = String(severity).toLowerCase()
            if (key.includes('error') || key.includes('critical')) return 'rgba(190, 76, 88, 0.78)'
            if (key.includes('warn')) return 'rgba(209, 105, 40, 0.78)'
            return 'rgba(75, 111, 193, 0.72)'
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
.ol-monitor-container {
    position: relative;
    width: 100%;
    height: 100%;
    background-color: rgba(243, 243, 243, 1);
    display: flex;
    justify-content: center;

    .ol-major-container {
        position: relative;
        width: 100%;
        max-width: 1500px;
        height: 100%;
        min-height: 0;
        display: flex;
        flex-direction: column;
    }

    .ol-title-block {
        @include HbetweenVcenter;

        position: absolute;
        width: 100%;
        padding: 22px 18px 12px;
        z-index: 2;
        gap: 16px;
        background: rgba(243, 243, 243, 0.86);
        backdrop-filter: blur(18px);

        .title-copy {
            min-width: 220px;
            overflow: hidden;
        }

        .main-title {
            font-size: 26px;
            font-weight: 600;
            color: rgba(30, 32, 38, 1);
            line-height: 1.2;
        }

        .sub-title {
            @include nowrap;

            max-width: min(720px, 52vw);
            margin-top: 5px;
            font-size: 13px;
            color: rgba(87, 91, 101, 1);
        }

        .right-block {
            @include HendVcenter;

            flex: 1;
            gap: 8px;
            min-width: 360px;

            .lake-input {
                width: min(360px, 32vw);
                min-width: 220px;
            }
        }
    }

    .ol-content-block {
        @include narrow-scroll-bar;

        position: relative;
        width: 100%;
        height: 100%;
        min-height: 0;
        padding: 104px 18px 18px;
        overflow: overlay;
    }
}

.error-banner,
.status-strip,
.monitor-panel,
.kpi-card {
    border: 1px solid rgba(42, 47, 56, 0.08);
    border-radius: 8px;
    background: rgba(255, 255, 255, 0.92);
    box-shadow: 0 12px 26px rgba(28, 32, 40, 0.06);
}

.error-banner {
    @include Vcenter;

    gap: 8px;
    min-height: 44px;
    margin-bottom: 10px;
    padding: 0 14px;
    color: rgba(157, 48, 58, 1);
    background: rgba(255, 246, 246, 0.96);
}

.status-strip {
    @include Vcenter;

    gap: 12px;
    min-height: 72px;
    padding: 12px;

    .health-score {
        @include HcenterVcenterC;

        width: 52px;
        height: 52px;
        flex-shrink: 0;
        border-radius: 50%;
        color: white;

        &.good {
            background: rgba(20, 155, 124, 1);
        }

        &.warn {
            background: rgba(209, 105, 40, 1);
        }

        &.bad {
            background: rgba(190, 76, 88, 1);
        }

        span {
            font-size: 18px;
            font-weight: 700;
            line-height: 1;
        }

        p {
            margin-top: 3px;
            font-size: 10px;
            line-height: 1;
        }
    }

    .status-meta {
        flex: 1;
        min-width: 0;

        .status-title {
            font-size: 17px;
            font-weight: 600;
            color: rgba(35, 38, 45, 1);
        }

        .status-subtitle {
            @include nowrap;

            margin-top: 5px;
            color: rgba(92, 97, 107, 1);
            font-size: 13px;
        }
    }

    .warning-pill {
        @include HcenterVcenter;

        gap: 6px;
        min-width: 58px;
        height: 36px;
        border-radius: 18px;
        color: rgba(20, 120, 96, 1);
        background: rgba(20, 155, 124, 0.12);

        &.active {
            color: rgba(181, 83, 20, 1);
            background: rgba(209, 105, 40, 0.14);
        }
    }
}

.kpi-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
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
            color: rgba(92, 97, 107, 1);
        }

        .kpi-value {
            margin-top: 4px;
            font-size: 24px;
            font-weight: 700;
            color: rgba(30, 32, 38, 1);
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

.chart-grid,
.deep-grid {
    display: grid;
    gap: 10px;
    margin-top: 10px;
}

.chart-grid.primary {
    grid-template-columns: minmax(0, 1.35fr) minmax(320px, 0.65fr);
}

.chart-grid.secondary {
    grid-template-columns: repeat(3, minmax(0, 1fr));
}

.deep-grid {
    grid-template-columns: minmax(0, 1fr) 360px;
}

.monitor-panel {
    display: flex;
    flex-direction: column;
    min-width: 0;
    min-height: 230px;
    padding: 12px;
    overflow: hidden;

    .panel-head {
        @include HbetweenVcenter;

        height: 30px;
        gap: 12px;
        margin-bottom: 8px;
        flex-shrink: 0;

        p {
            @include nowrap;

            font-size: 15px;
            font-weight: 600;
            color: rgba(34, 37, 43, 1);
        }

        span {
            @include nowrap;

            font-size: 12px;
            color: rgba(94, 100, 112, 1);
        }
    }

    :deep(.ol-chart-shell) {
        flex: 1;
        min-height: 0;
    }
}

.embedding-block {
    display: grid;
    grid-template-rows: minmax(0, 1fr) auto;
    gap: 8px;
    flex: 1;
    min-height: 0;

    .embedding-meta {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 6px;

        p {
            @include nowrap;

            padding: 6px 8px;
            border-radius: 6px;
            background: rgba(244, 246, 250, 1);
            color: rgba(31, 35, 43, 1);
            font-size: 12px;
        }

        span {
            margin-right: 6px;
            color: rgba(100, 106, 118, 1);
        }
    }
}

.table-panel {
    min-height: 280px;

    .table-health {
        @include narrow-scroll-bar;

        display: grid;
        align-content: start;
        gap: 6px;
        flex: 1;
        min-height: 0;
        overflow: overlay;
    }

    .table-row {
        display: grid;
        grid-template-columns: 18px minmax(150px, 1fr) 86px 86px 150px;
        align-items: center;
        gap: 8px;
        width: 100%;
        min-height: 36px;
        padding: 0 8px;
        border: none;
        border-radius: 6px;
        background: rgba(247, 248, 251, 1);
        color: rgba(38, 42, 50, 1);
        font-size: 12px;
        text-align: left;
        cursor: pointer;

        &:hover {
            background: rgba(238, 241, 247, 1);
        }

        .table-status {
            width: 9px;
            height: 9px;
            border-radius: 50%;
            background: rgba(190, 76, 88, 1);

            &.ok {
                background: rgba(20, 155, 124, 1);
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

.detail-panel {
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
    min-height: 280px;
    margin-top: 10px;

    .tab-controls {
        @include Vcenter;

        gap: 5px;
        flex-wrap: wrap;

        button {
            height: 30px;
            padding: 0 10px;
            border: 1px solid rgba(48, 55, 67, 0.1);
            border-radius: 6px;
            background: rgba(246, 247, 250, 1);
            color: rgba(69, 75, 88, 1);
            cursor: pointer;

            &.active {
                color: white;
                background: rgba(75, 111, 193, 1);
            }
        }
    }

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
            color: rgba(36, 40, 48, 1);
            font-size: 12px;
            text-align: left;
            vertical-align: top;
            word-break: break-word;
        }

        th {
            color: rgba(89, 96, 109, 1);
            font-weight: 600;
            background: rgba(247, 248, 251, 1);
        }

        tbody tr {
            cursor: pointer;

            &:hover {
                background: rgba(246, 248, 252, 1);
            }
        }
    }
}

@media (max-width: 1180px) {
    .ol-monitor-container {
        .ol-title-block {
            align-items: flex-start;
            flex-direction: column;

            .right-block {
                width: 100%;
                justify-content: flex-start;

                .lake-input {
                    width: 100%;
                    flex: 1;
                }
            }
        }

        .ol-content-block {
            padding-top: 154px;
        }
    }

    .kpi-grid,
    .chart-grid.secondary {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .chart-grid.primary,
    .deep-grid {
        grid-template-columns: 1fr;
    }
}

@media (max-width: 720px) {
    .ol-monitor-container {
        .ol-title-block {
            padding: 14px 12px 10px;

            .main-title {
                font-size: 22px;
            }

            .sub-title {
                max-width: 100%;
            }

            .right-block {
                flex-wrap: wrap;
                min-width: 0;
            }
        }

        .ol-content-block {
            padding: 174px 12px 12px;
        }
    }

    .kpi-grid,
    .chart-grid.secondary {
        grid-template-columns: 1fr;
    }

    .table-panel {
        .table-row {
            grid-template-columns: 18px minmax(110px, 1fr) 72px 72px;

            .table-time {
                display: none;
            }
        }
    }
}
</style>
