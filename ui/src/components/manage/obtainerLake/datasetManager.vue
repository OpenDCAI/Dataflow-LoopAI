<template>
    <div class="dataset-manager">
        <div v-if="error" class="dm-error-banner">
            <i class="ms-Icon ms-Icon--ErrorBadge"></i>
            <span>{{ error }}</span>
            <button type="button" class="dm-error-close" title="关闭" @click="error = ''">
                <i class="ms-Icon ms-Icon--ChromeClose"></i>
            </button>
        </div>

        <header class="dm-head">
            <div class="dm-head-copy">
                <h3>内部数据集</h3>
                <p>{{ datasets.length }} 个数据集 · {{ totalRecords }} 条记录</p>
            </div>
            <div class="dm-head-actions">
                <input
                    v-model="search"
                    class="dm-search"
                    type="text"
                    placeholder="按名称 / ID 搜索"
                />
                <button type="button" class="dm-btn" :disabled="loading" @click="refresh">
                    <i class="ms-Icon ms-Icon--Refresh" :class="{ spinning: loading }"></i>
                    {{ loading ? '刷新中' : '刷新' }}
                </button>
                <button type="button" class="dm-btn dm-btn-primary" @click="openIngest()">
                    <i class="ms-Icon ms-Icon--Upload"></i>
                    导入数据
                </button>
            </div>
        </header>

        <section class="dm-table-wrap">
            <table class="dm-table">
                <thead>
                    <tr>
                        <th>名称</th>
                        <th>ID</th>
                        <th>记录数</th>
                        <th>质量层级</th>
                        <th>领域</th>
                        <th>许可证</th>
                        <th>来源</th>
                        <th>最近导入</th>
                        <th class="dm-col-actions">操作</th>
                    </tr>
                </thead>
                <tbody>
                    <tr v-if="loading && !datasets.length">
                        <td colspan="9" class="dm-empty">正在加载数据集…</td>
                    </tr>
                    <tr v-else-if="!filteredDatasets.length">
                        <td colspan="9" class="dm-empty">暂无内部数据集，点击「导入数据」创建</td>
                    </tr>
                    <tr v-for="ds in filteredDatasets" :key="ds.id" :class="{ selected: selected?.id === ds.id }">
                        <td>
                            <button type="button" class="dm-link" @click="openDetail(ds)">{{ ds.name || '-' }}</button>
                        </td>
                        <td class="dm-mono" :title="ds.id">{{ shortId(ds.id) }}</td>
                        <td class="dm-num">{{ formatNumber(ds.n_samples) }}</td>
                        <td>
                            <span v-if="qualityChips(ds).length" class="dm-chips">
                                <span v-for="chip in qualityChips(ds)" :key="chip.label" class="dm-chip" :class="`lv-${chip.label.toLowerCase()}`">
                                    {{ chip.label }} · {{ formatNumber(chip.count) }}
                                </span>
                            </span>
                            <span v-else class="dm-dim">-</span>
                        </td>
                        <td>
                            <span v-if="domainChips(ds).length" class="dm-chips">
                                <span v-for="chip in domainChips(ds).slice(0, 2)" :key="chip" class="dm-chip dm-chip-domain">{{ chip }}</span>
                                <span v-if="domainChips(ds).length > 2" class="dm-chip dm-chip-domain" :title="domainChips(ds).join(', ')">
                                    +{{ domainChips(ds).length - 2 }}
                                </span>
                            </span>
                            <span v-else class="dm-dim">-</span>
                        </td>
                        <td class="dm-dim">{{ ds.license || '-' }}</td>
                        <td class="dm-dim" :title="ds.source">{{ ds.source || '-' }}</td>
                        <td class="dm-dim">{{ formatTime(ds.last_ingested_at) }}</td>
                        <td class="dm-col-actions">
                            <div class="dm-row-actions">
                                <button type="button" class="dm-icon-btn" title="查看详情" @click="openDetail(ds)">
                                    <i class="ms-Icon ms-Icon--Info"></i>
                                </button>
                                <button type="button" class="dm-icon-btn" title="导出 JSONL" @click="exportDataset(ds)">
                                    <i class="ms-Icon ms-Icon--Download"></i>
                                </button>
                                <button type="button" class="dm-icon-btn" title="清空样本" @click="clearSamples(ds)">
                                    <i class="ms-Icon ms-Icon--Clear"></i>
                                </button>
                                <button type="button" class="dm-icon-btn dm-danger" title="删除数据集" @click="deleteDataset(ds)">
                                    <i class="ms-Icon ms-Icon--Delete"></i>
                                </button>
                            </div>
                        </td>
                    </tr>
                </tbody>
            </table>
        </section>

        <section v-if="exportNotice" class="dm-notice">
            <i class="ms-Icon ms-Icon--CheckMark"></i>
            <span>已导出：</span>
            <code @click="copyText(exportNotice)">{{ exportNotice }}</code>
            <span class="dm-copy-hint">（点击复制）</span>
            <button type="button" class="dm-btn" @click="downloadExport" :disabled="downloading">
                {{ downloading ? '下载中…' : '下载到浏览器' }}
            </button>
        </section>

        <!-- Detail drawer -->
        <transition name="dm-drawer">
            <div v-if="selected" class="dm-drawer-mask" @click.self="closeDetail">
                <aside class="dm-drawer">
                    <header class="dm-drawer-head">
                        <div>
                            <h4>{{ selected.name || selected.id }}</h4>
                            <p class="dm-mono">{{ selected.id }}</p>
                        </div>
                        <button type="button" class="dm-icon-btn" title="关闭" @click="closeDetail">
                            <i class="ms-Icon ms-Icon--ChromeClose"></i>
                        </button>
                    </header>

                    <div v-if="detailError" class="dm-drawer-error">{{ detailError }}</div>

                    <div class="dm-drawer-body">
                        <section class="dm-block">
                            <div class="dm-block-title">
                                <span>元数据</span>
                                <button type="button" class="dm-btn dm-btn-small" :disabled="saving" @click="saveMetadata">
                                    {{ saving ? '保存中' : '保存' }}
                                </button>
                            </div>
                            <div class="dm-form">
                                <label><span>名称</span>
                                    <input v-model="metaForm.name" type="text" />
                                </label>
                                <label><span>来源</span>
                                    <input v-model="metaForm.source" type="text" />
                                </label>
                                <label><span>许可证</span>
                                    <input v-model="metaForm.license" type="text" />
                                </label>
                                <label><span>负责人</span>
                                    <input v-model="metaForm.owner" type="text" />
                                </label>
                                <label class="dm-form-wide"><span>描述</span>
                                    <textarea v-model="metaForm.description" rows="2"></textarea>
                                </label>
                            </div>
                        </section>

                        <section class="dm-block">
                            <div class="dm-block-title"><span>统计</span></div>
                            <div class="dm-stats">
                                <div class="dm-stat"><span>记录数</span><strong>{{ formatNumber(selected.n_samples) }}</strong></div>
                                <div class="dm-stat"><span>创建时间</span><strong>{{ formatTime(selected.created_at) }}</strong></div>
                                <div class="dm-stat"><span>最近导入</span><strong>{{ formatTime(selected.last_ingested_at) }}</strong></div>
                                <div class="dm-stat"><span>质量层级</span><strong>{{ qualitySummary }}</strong></div>
                            </div>
                        </section>

                        <section class="dm-block">
                            <div class="dm-block-title"><span>记录预览</span></div>
                            <div v-if="previewLoading" class="dm-dim">加载中…</div>
                            <div v-else-if="previewError" class="dm-drawer-error">{{ previewError }}</div>
                            <div v-else-if="previewRows.length" class="dm-preview">
                                <table class="dm-table dm-table-compact">
                                    <thead>
                                        <tr>
                                            <th v-for="col in previewColumns" :key="col">{{ col }}</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        <tr v-for="(row, index) in previewRows" :key="index">
                                            <td v-for="col in previewColumns" :key="col" :title="cellText(row[col])">
                                                {{ cellText(row[col]) }}
                                            </td>
                                        </tr>
                                    </tbody>
                                </table>
                            </div>
                            <div v-else class="dm-dim">该数据集暂无记录</div>
                        </section>

                        <section class="dm-block dm-block-actions">
                            <button type="button" class="dm-btn" :disabled="busy" @click="exportDataset(selected)">
                                <i class="ms-Icon ms-Icon--Download"></i>
                                导出 JSONL
                            </button>
                            <button type="button" class="dm-btn" :disabled="busy" @click="openIngest(selected.name)">
                                <i class="ms-Icon ms-Icon--Upload"></i>
                                追加导入
                            </button>
                            <button type="button" class="dm-btn dm-danger" :disabled="busy" @click="clearSamples(selected)">
                                <i class="ms-Icon ms-Icon--Clear"></i>
                                清空样本
                            </button>
                            <button type="button" class="dm-btn dm-danger" :disabled="busy" @click="deleteDataset(selected)">
                                <i class="ms-Icon ms-Icon--Delete"></i>
                                删除数据集
                            </button>
                        </section>
                    </div>
                </aside>
            </div>
        </transition>

        <!-- Ingest dialog -->
        <div v-if="showIngest" class="dm-modal-mask" @click.self="showIngest = false">
            <div class="dm-modal">
                <header class="dm-modal-head">
                    <h4>导入数据到内部数据集</h4>
                    <button type="button" class="dm-icon-btn" title="关闭" @click="showIngest = false">
                        <i class="ms-Icon ms-Icon--ChromeClose"></i>
                    </button>
                </header>
                <div v-if="ingestError" class="dm-drawer-error">{{ ingestError }}</div>
                <div class="dm-modal-body">
                    <div class="dm-form">
                        <label class="dm-form-wide"><span>数据集名称 <em>*</em></span>
                            <input v-model="ingestForm.dataset" type="text" placeholder="已存在则追加，不存在则新建" />
                        </label>
                        <label class="dm-form-file"><span>JSONL 文件 <em>*</em></span>
                            <input ref="fileInput" type="file" accept=".jsonl,.json,.ndjson,application/json,text/plain" @change="onFileChange" />
                            <span v-if="ingestForm.fileName" class="dm-file-name">{{ ingestForm.fileName }} · {{ formatBytes(ingestForm.fileSize) }}</span>
                        </label>
                        <label><span>质量层级 <em>*</em></span>
                            <select v-model="ingestForm.qualityLevel">
                                <option v-for="lv in qualityLevels" :key="lv" :value="lv">{{ lv }}</option>
                            </select>
                        </label>
                        <label><span>阶段</span>
                            <input v-model="ingestForm.stage" type="text" placeholder="sft / pretrain" />
                        </label>
                        <label><span>领域</span>
                            <input v-model="ingestForm.domain" type="text" placeholder="code / math / web" />
                        </label>
                        <label><span>语言</span>
                            <input v-model="ingestForm.lang" type="text" placeholder="zh / en" />
                        </label>
                        <label><span>来源</span>
                            <input v-model="ingestForm.source" type="text" />
                        </label>
                        <label><span>许可证</span>
                            <input v-model="ingestForm.license" type="text" />
                        </label>
                        <label><span>任务类型</span>
                            <input v-model="ingestForm.taskType" type="text" placeholder="SFT / preference" />
                        </label>
                        <label><span>处理层级</span>
                            <input v-model="ingestForm.processingLevel" type="text" placeholder="raw / normalized" />
                        </label>
                        <label><span>来源类型</span>
                            <input v-model="ingestForm.sourceKind" type="text" placeholder="huggingface / web / fixture" />
                        </label>
                        <label class="dm-form-wide"><span>附加标签（可选）</span>
                            <input v-model="ingestForm.tags" type="text" placeholder="key=value,key2=value2" />
                        </label>
                    </div>
                </div>
                <footer class="dm-modal-foot">
                    <button type="button" class="dm-btn" :disabled="ingesting" @click="showIngest = false">取消</button>
                    <button type="button" class="dm-btn dm-btn-primary" :disabled="ingesting || !ingestForm.dataset || !ingestForm.fileContent" @click="submitIngest">
                        <i class="ms-Icon ms-Icon--Sync" :class="{ spinning: ingesting }"></i>
                        {{ ingesting ? '导入中…' : '开始导入' }}
                    </button>
                </footer>
            </div>
        </div>
    </div>
</template>

<script>
import { runDataMixerCli } from '@/services/obtainerLake'

export default {
    name: 'DatasetManager',
    props: {
        lakePath: {
            type: String,
            default: '.datamixer/lake.yaml'
        },
        warehouse: {
            type: String,
            default: ''
        }
    },
    data() {
        return {
            datasets: [],
            loading: false,
            error: '',
            search: '',
            selected: null,
            detailError: '',
            detailLoading: false,
            previewRows: [],
            previewColumns: [],
            previewLoading: false,
            previewError: '',
            metaForm: {
                name: '',
                source: '',
                license: '',
                owner: '',
                description: ''
            },
            saving: false,
            busy: false,
            exportNotice: '',
            downloading: false,
            showIngest: false,
            ingesting: false,
            ingestError: '',
            ingestForm: {
                dataset: '',
                fileName: '',
                fileSize: 0,
                fileContent: '',
                qualityLevel: 'L3',
                stage: '',
                domain: '',
                lang: '',
                source: '',
                license: '',
                taskType: '',
                processingLevel: '',
                sourceKind: '',
                tags: ''
            },
            qualityLevels: ['L1', 'L2', 'L3', 'L4']
        }
    },
    computed: {
        totalRecords() {
            return this.datasets.reduce((sum, ds) => sum + Number(ds.n_samples || 0), 0)
        },
        filteredDatasets() {
            const keyword = this.search.trim().toLowerCase()
            if (!keyword) return this.datasets
            return this.datasets.filter((ds) => {
                return `${ds.name || ''} ${ds.id || ''}`.toLowerCase().includes(keyword)
            })
        },
        qualitySummary() {
            const chips = this.selected ? this.qualityChips(this.selected) : []
            return chips.length ? chips.map((c) => `${c.label}:${c.count}`).join(' · ') : '-'
        }
    },
    methods: {
        async dm(line, files) {
            const res = await runDataMixerCli({ lake: this.lakePath, line, files })
            if (res?.code && res.code !== 200) {
                throw new Error(res.message || '命令执行失败')
            }
            const payload = res.data || {}
            if (payload.exit !== 0) {
                throw new Error(payload.output?.error || payload.raw || `命令退出码 ${payload.exit}`)
            }
            return payload.output || {}
        },
        async refresh() {
            this.loading = true
            this.error = ''
            try {
                const output = await this.dm('dataset list')
                this.datasets = Array.isArray(output.datasets) ? output.datasets : []
            } catch (err) {
                this.error = err?.message || '加载数据集失败'
            } finally {
                this.loading = false
            }
        },
        async openDetail(ds) {
            this.selected = ds
            this.detailError = ''
            this.detailLoading = true
            this.previewRows = []
            this.previewColumns = []
            this.metaForm = {
                name: ds.name || '',
                source: ds.source || '',
                license: ds.license || '',
                owner: ds.owner || '',
                description: ds.description || ''
            }
            try {
                const detail = await this.dm(`dataset show ${this.shellQuote(ds.name || ds.id)}`)
                if (detail && typeof detail === 'object') {
                    this.selected = { ...ds, ...detail }
                }
            } catch (err) {
                this.detailError = err?.message || '加载详情失败'
            } finally {
                this.detailLoading = false
            }
            await this.loadPreview(ds)
        },
        async loadPreview(ds) {
            this.previewLoading = true
            this.previewError = ''
            try {
                const output = await this.dm(
                    `query --dataset ${this.shellQuote(ds.name || ds.id)} --limit 20`
                )
                const rows = Array.isArray(output.rows) ? output.rows : []
                this.previewRows = rows
                const columns = new Set(['sample_id'])
                rows.forEach((row) => Object.keys(row).forEach((key) => columns.add(key)))
                this.previewColumns = Array.from(columns)
            } catch (err) {
                this.previewError = err?.message || '加载记录预览失败'
            } finally {
                this.previewLoading = false
            }
        },
        closeDetail() {
            this.selected = null
        },
        async saveMetadata() {
            const ds = this.selected
            if (!ds) return
            this.saving = true
            this.detailError = ''
            try {
                const args = []
                const fields = ['name', 'source', 'license', 'owner', 'description']
                fields.forEach((key) => {
                    const value = String(this.metaForm[key] || '').trim()
                    if (value) args.push(`--${key}`, this.shellQuote(value))
                })
                if (!args.length) return
                const updated = await this.dm(`dataset update ${this.shellQuote(ds.name || ds.id)} ${args.join(' ')}`)
                this.selected = { ...this.selected, ...updated }
                const row = this.datasets.find((item) => item.id === ds.id)
                if (row) Object.assign(row, updated)
            } catch (err) {
                this.detailError = err?.message || '保存元数据失败'
            } finally {
                this.saving = false
            }
        },
        defaultExportPath(ds) {
            const base = this.warehouse || './outputs'
            const name = String(ds.name || ds.id || 'dataset').replace(/[^a-zA-Z0-9_-]/g, '_')
            const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\..+/, '')
            return `${base}/exports/${name}-${stamp}.jsonl`
        },
        async exportDataset(ds) {
            const target = this.selected?.id === ds.id ? this.selected : ds
            this.busy = true
            this.error = ''
            this.exportNotice = ''
            try {
                const out = this.defaultExportPath(target)
                const output = await this.dm(
                    `export-jsonl --dataset ${this.shellQuote(target.name || target.id)} --out ${this.shellQuote(out)} --field raw_content`
                )
                this.exportNotice = output.out || out
            } catch (err) {
                this.error = err?.message || '导出失败'
            } finally {
                this.busy = false
            }
        },
        async downloadExport() {
            const uri = this.exportNotice
            if (!uri) return
            this.downloading = true
            try {
                window.open(`/api/obtainer/export/download?uri=${encodeURIComponent(uri)}`, '_blank')
            } finally {
                this.downloading = false
            }
        },
        async clearSamples(ds) {
            const name = ds.name || ds.id
            if (!window.confirm(`确认清空数据集「${name}」的全部样本？该操作不可恢复。`)) return
            this.busy = true
            this.error = ''
            try {
                const output = await this.dm(
                    `erase --dataset ${this.shellQuote(name)} --reason webui-clear-samples`
                )
                const erased = Number(output.erased || 0)
                window.alert(`已清空 ${erased} 条样本`)
                if (this.selected?.id === ds.id) {
                    this.selected.n_samples = 0
                }
                await this.refresh()
            } catch (err) {
                this.error = err?.message || '清空样本失败'
            } finally {
                this.busy = false
            }
        },
        async deleteDataset(ds) {
            const name = ds.name || ds.id
            if (!window.confirm(`确认删除数据集「${name}」？其全部样本、内容与索引都会被删除，不可恢复。`)) return
            this.busy = true
            this.error = ''
            try {
                await this.dm(`dataset delete ${this.shellQuote(name)} --yes --reason webui-delete-dataset`)
                if (this.selected?.id === ds.id) this.selected = null
                await this.refresh()
            } catch (err) {
                this.error = err?.message || '删除数据集失败'
            } finally {
                this.busy = false
            }
        },
        openIngest(datasetName) {
            this.ingestError = ''
            this.ingestForm.dataset = datasetName || ''
            this.ingestForm.fileName = ''
            this.ingestForm.fileSize = 0
            this.ingestForm.fileContent = ''
            if (this.$refs.fileInput) this.$refs.fileInput.value = ''
            this.showIngest = true
        },
        onFileChange(event) {
            const file = event.target.files && event.target.files[0]
            if (!file) {
                this.ingestForm.fileName = ''
                this.ingestForm.fileSize = 0
                this.ingestForm.fileContent = ''
                return
            }
            this.ingestForm.fileName = file.name
            this.ingestForm.fileSize = file.size
            const reader = new FileReader()
            reader.onload = () => {
                this.ingestForm.fileContent = String(reader.result || '')
            }
            reader.onerror = () => {
                this.ingestError = '读取文件失败'
            }
            reader.readAsText(file)
        },
        buildIngestLine() {
            const form = this.ingestForm
            const parts = ['ingest', this.shellQuote(form.dataset), '--file', '@__dm_file__', '--quality-level', form.qualityLevel]
            const optional = [
                ['stage', form.stage],
                ['domain', form.domain],
                ['lang', form.lang],
                ['source', form.source],
                ['license', form.license],
                ['task-type', form.taskType],
                ['processing-level', form.processingLevel],
                ['source-kind', form.sourceKind]
            ]
            optional.forEach(([flag, value]) => {
                const text = String(value || '').trim()
                if (text) parts.push(`--${flag}`, this.shellQuote(text))
            })
            const tags = String(form.tags || '').trim()
            if (tags) {
                tags.split(/[,，]/).forEach((tag) => {
                    const item = tag.trim()
                    if (item) parts.push('--tag', this.shellQuote(item))
                })
            }
            return parts.join(' ')
        },
        async submitIngest() {
            if (!this.ingestForm.dataset || !this.ingestForm.fileContent) return
            this.ingesting = true
            this.ingestError = ''
            try {
                const line = this.buildIngestLine()
                const files = { '@__dm_file__': this.ingestForm.fileContent }
                const output = await this.dm(line, files)
                const written = Number(output.written || output.ingested || 0)
                window.alert(`导入完成：${written} 条记录写入数据集「${this.ingestForm.dataset}」`)
                this.showIngest = false
                if (this.selected && this.ingestForm.dataset === this.selected.name) {
                    this.selected.n_samples = Number(this.selected.n_samples || 0) + written
                }
                await this.refresh()
            } catch (err) {
                this.ingestError = err?.message || '导入失败'
            } finally {
                this.ingesting = false
            }
        },
        qualityChips(ds) {
            const levels = ds.quality_levels || {}
            return Object.keys(levels)
                .sort()
                .map((label) => ({ label, count: Number(levels[label] || 0) }))
        },
        domainChips(ds) {
            const domains = ds.domains || {}
            return Object.keys(domains).sort((a, b) => Number(domains[b] || 0) - Number(domains[a] || 0))
        },
        shortId(id) {
            return String(id || '').length > 12 ? `${String(id).slice(0, 12)}…` : String(id || '-')
        },
        shellQuote(value) {
            return `'${String(value).replace(/'/g, "'\\''")}'`
        },
        cellText(value) {
            if (value === null || value === undefined) return ''
            if (typeof value === 'object') return JSON.stringify(value)
            return String(value)
        },
        formatNumber(value) {
            const number = Number(value || 0)
            return Number.isFinite(number) ? new Intl.NumberFormat('zh-CN').format(number) : '0'
        },
        formatBytes(bytes) {
            const value = Number(bytes || 0)
            if (!value) return '0 B'
            const units = ['B', 'KB', 'MB', 'GB']
            let index = 0
            let size = value
            while (size >= 1024 && index < units.length - 1) {
                size /= 1024
                index += 1
            }
            return `${size.toFixed(index ? 1 : 0)} ${units[index]}`
        },
        formatTime(value) {
            if (!value) return '-'
            const date = new Date(Number(value) * 1000)
            if (Number.isNaN(date.getTime())) return '-'
            const pad = (n) => String(n).padStart(2, '0')
            return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
        },
        copyText(text) {
            const value = String(text || '')
            if (!value) return
            if (navigator.clipboard?.writeText) {
                navigator.clipboard.writeText(value).catch(() => {})
                return
            }
            const area = document.createElement('textarea')
            area.value = value
            area.style.position = 'fixed'
            area.style.opacity = '0'
            document.body.appendChild(area)
            area.select()
            document.execCommand('copy')
            document.body.removeChild(area)
        }
    },
    mounted() {
        this.refresh()
    }
}
</script>

<style lang="scss" scoped>
.dataset-manager {
    display: grid;
    gap: 12px;
    color: var(--lp-text);
}

.dm-error-banner, .dm-notice {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 9px 12px;
    border-radius: 6px;
    font-size: 12px;
}
.dm-error-banner {
    background: rgba(180, 35, 42, 0.08);
    color: #b4232a;
    border: 1px solid rgba(180, 35, 42, 0.18);
}
.dm-error-banner .dm-error-close {
    margin-left: auto;
    background: none;
    border: 0;
    cursor: pointer;
    color: inherit;
}
.dm-notice {
    background: rgba(33, 163, 102, 0.08);
    color: #168258;
    border: 1px solid rgba(33, 163, 102, 0.2);
    code {
        cursor: pointer;
        font-size: 11px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
}
.dm-copy-hint { font-size: 11px; opacity: 0.8; }

.dm-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
    h3 { margin: 0; font-size: 15px; font-weight: 700; }
    p { margin: 3px 0 0; font-size: 11px; color: rgba(65, 65, 65, 0.62); }
}
.dm-head-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.dm-search {
    width: 200px;
    height: 30px;
    padding: 0 10px;
    border: 1px solid rgba(90, 45, 133, 0.22);
    border-radius: 6px;
    font-size: 12px;
    background: #fff;
    outline: none;
    &:focus { border-color: rgba(90, 45, 133, 0.55); }
}

.dm-btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    height: 30px;
    padding: 0 12px;
    border: 1px solid rgba(90, 45, 133, 0.25);
    border-radius: 6px;
    background: #fff;
    color: var(--lp-text);
    font-size: 12px;
    cursor: pointer;
    transition: background 0.15s ease;
    &:hover:not(:disabled) { background: rgba(90, 45, 133, 0.06); }
    &:disabled { opacity: 0.5; cursor: not-allowed; }
}
.dm-btn-primary {
    background: rgba(90, 45, 133, 0.92);
    border-color: rgba(90, 45, 133, 0.92);
    color: #fff;
    &:hover:not(:disabled) { background: rgba(90, 45, 133, 1); }
}
.dm-btn-small { height: 24px; padding: 0 10px; font-size: 11px; }
.dm-danger { color: #b4232a; border-color: rgba(180, 35, 42, 0.3); }
.spinning { animation: dm-spin 0.9s linear infinite; display: inline-block; }
@keyframes dm-spin { to { transform: rotate(360deg); } }

.dm-table-wrap {
    overflow: hidden;
    border: 1px solid rgba(90, 45, 133, 0.14);
    border-radius: 8px;
    background: #fff;
}
.dm-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
    th, td {
        padding: 8px 10px;
        text-align: left;
        border-bottom: 1px solid rgba(90, 45, 133, 0.08);
        white-space: nowrap;
    }
    th {
        background: rgba(90, 45, 133, 0.05);
        color: var(--lp-text);
        font-size: 11px;
        font-weight: 700;
    }
    tbody tr:hover { background: rgba(90, 45, 133, 0.03); }
    tbody tr.selected { background: rgba(90, 45, 133, 0.08); }
}
.dm-table-compact { font-size: 11px; th, td { padding: 5px 8px; max-width: 180px; overflow: hidden; text-overflow: ellipsis; } }
.dm-col-actions { text-align: right; }
.dm-row-actions { display: flex; justify-content: flex-end; gap: 4px; }
.dm-empty { text-align: center; color: rgba(65, 65, 65, 0.55); padding: 28px 0 !important; }
.dm-dim { color: rgba(65, 65, 65, 0.55); }
.dm-num { text-align: right; font-weight: 700; }
.dm-mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; color: rgba(65, 65, 65, 0.7); }
.dm-link {
    background: none;
    border: 0;
    padding: 0;
    color: rgba(90, 45, 133, 0.92);
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    &:hover { text-decoration: underline; }
}
.dm-chips { display: inline-flex; gap: 4px; flex-wrap: wrap; }
.dm-chip {
    padding: 1px 6px;
    border-radius: 9px;
    font-size: 10px;
    background: rgba(90, 45, 133, 0.09);
    color: rgba(90, 45, 133, 0.92);
}
.dm-chip-domain { background: rgba(64, 99, 170, 0.1); color: rgba(64, 99, 170, 0.95); }
.dm-chip.lv-l1 { background: rgba(217, 144, 0, 0.12); color: #9a6500; }
.dm-chip.lv-l2 { background: rgba(64, 99, 170, 0.12); color: #4063aa; }
.dm-chip.lv-l3 { background: rgba(33, 163, 102, 0.12); color: #168258; }
.dm-chip.lv-l4 { background: rgba(90, 45, 133, 0.14); color: #5a2d85; }

.dm-icon-btn {
    width: 26px;
    height: 26px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border: 1px solid rgba(90, 45, 133, 0.18);
    border-radius: 5px;
    background: #fff;
    color: var(--lp-text);
    cursor: pointer;
    font-size: 12px;
    transition: background 0.15s ease;
    &:hover { background: rgba(90, 45, 133, 0.08); color: rgba(90, 45, 133, 0.95); }
}
.dm-danger.dm-icon-btn:hover { background: rgba(180, 35, 42, 0.08); color: #b4232a; }

.dm-drawer-mask {
    position: fixed;
    inset: 0;
    background: rgba(20, 20, 30, 0.34);
    z-index: 1000;
}
.dm-drawer {
    position: absolute;
    top: 0;
    right: 0;
    width: min(640px, 92vw);
    height: 100%;
    background: #faf9fc;
    box-shadow: -8px 0 24px rgba(30, 20, 50, 0.16);
    display: flex;
    flex-direction: column;
}
.dm-drawer-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 10px;
    padding: 14px 16px;
    border-bottom: 1px solid rgba(90, 45, 133, 0.12);
    h4 { margin: 0; font-size: 15px; }
    p { margin: 3px 0 0; font-size: 11px; }
}
.dm-drawer-error {
    margin: 12px 16px 0;
    padding: 8px 10px;
    border-radius: 6px;
    background: rgba(180, 35, 42, 0.08);
    color: #b4232a;
    font-size: 12px;
}
.dm-drawer-body {
    flex: 1;
    overflow: auto;
    padding: 14px 16px 20px;
    display: grid;
    gap: 14px;
    align-content: start;
}
.dm-block {
    background: #fff;
    border: 1px solid rgba(90, 45, 133, 0.12);
    border-radius: 8px;
    padding: 12px;
}
.dm-block-title {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 10px;
    font-size: 12px;
    font-weight: 700;
    color: var(--lp-text);
}
.dm-block-actions { display: flex; gap: 8px; flex-wrap: wrap; }

.dm-form {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px 12px;
    label {
        display: grid;
        gap: 4px;
        font-size: 11px;
        color: var(--lp-text);
        span em { color: #b4232a; font-style: normal; }
    }
    input, select, textarea {
        width: 100%;
        box-sizing: border-box;
        height: 28px;
        padding: 0 8px;
        border: 1px solid rgba(90, 45, 133, 0.2);
        border-radius: 5px;
        font-size: 12px;
        background: #fff;
        outline: none;
        &:focus { border-color: rgba(90, 45, 133, 0.55); }
    }
    textarea { height: auto; padding: 6px 8px; resize: vertical; }
}
.dm-form-wide { grid-column: 1 / -1; }
.dm-form-file input[type='file'] { height: auto; padding: 5px; }
.dm-file-name { font-size: 11px; color: rgba(65, 65, 65, 0.65); }

.dm-stats {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
    .dm-stat {
        padding: 8px 10px;
        border: 1px solid rgba(90, 45, 133, 0.1);
        border-radius: 6px;
        display: flex;
        flex-direction: column;
        gap: 3px;
        span { font-size: 10px; color: rgba(65, 65, 65, 0.6); }
        strong { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    }
}

.dm-preview { overflow: auto; max-height: 280px; }

.dm-modal-mask {
    position: fixed;
    inset: 0;
    background: rgba(20, 20, 30, 0.42);
    z-index: 1100;
    display: flex;
    align-items: center;
    justify-content: center;
}
.dm-modal {
    width: min(680px, 94vw);
    max-height: 88vh;
    background: #faf9fc;
    border-radius: 10px;
    box-shadow: 0 12px 40px rgba(30, 20, 50, 0.22);
    display: flex;
    flex-direction: column;
}
.dm-modal-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 16px;
    border-bottom: 1px solid rgba(90, 45, 133, 0.12);
    h4 { margin: 0; font-size: 15px; }
}
.dm-modal-body { flex: 1; overflow: auto; padding: 14px 16px; }
.dm-modal-foot {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    padding: 12px 16px;
    border-top: 1px solid rgba(90, 45, 133, 0.12);
}

.dm-drawer-enter-active, .dm-drawer-leave-active { transition: opacity 0.18s ease; }
.dm-drawer-enter-active .dm-drawer, .dm-drawer-leave-active .dm-drawer { transition: transform 0.18s ease; }
.dm-drawer-enter-from, .dm-drawer-leave-to { opacity: 0; }
.dm-drawer-enter-from .dm-drawer, .dm-drawer-leave-to .dm-drawer { transform: translateX(24px); }
</style>
