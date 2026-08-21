<template>
    <aside class="lp-tasks">
        <header class="lp-tasks__head">
            <span class="lp-label">{{ local('Tasks') }}</span>
            <div class="lp-tasks__head-actions">
                <button
                    type="button"
                    class="lp-btn lp-btn--ghost lp-btn--icon lp-btn--sm"
                    :class="{ 'is-on': showSearch }"
                    :title="local('Search Tasks ...')"
                    @click="toggleSearch"
                >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
                        <circle cx="11" cy="11" r="6.5" />
                        <path d="M16 16l4 4" />
                    </svg>
                </button>
                <button
                    type="button"
                    class="lp-btn lp-btn--icon lp-btn--sm"
                    :title="local('New Task')"
                    @click="startCreate"
                >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
                        <path d="M12 5v14M5 12h14" />
                    </svg>
                </button>
            </div>
        </header>

        <div v-if="showSearch" class="lp-tasks__search">
            <input
                ref="search"
                v-model="searchText"
                class="lp-input"
                type="text"
                :placeholder="local('Search Tasks ...')"
                @keydown.esc="closeSearch"
            />
        </div>

        <div v-if="creating" class="lp-tasks__search">
            <input
                ref="create"
                v-model="draftName"
                class="lp-input lp-input--mono"
                type="text"
                :placeholder="local('Task name, or leave blank')"
                @keydown.enter="confirmCreate"
                @keydown.esc="cancelCreate"
                @blur="confirmCreate"
            />
        </div>

        <div class="lp-tasks__list lp-scroll">
            <div
                v-for="item in visibleTasks"
                :key="item.id"
                class="lp-tasks__item"
                :class="{ 'is-current': isCurrent(item) }"
                @click="select(item)"
            >
                <template v-if="armedId === item.id">
                    <div class="lp-tasks__armed">
                        <span class="lp-tasks__armed-copy">{{ local('Delete this task?') }}</span>
                        <div class="lp-tasks__armed-actions">
                            <button type="button" class="lp-btn lp-btn--danger lp-btn--sm" @click.stop="confirmDelete(item)">
                                {{ local('Delete') }}
                            </button>
                            <button type="button" class="lp-btn lp-btn--ghost lp-btn--sm" @click.stop="armedId = null">
                                {{ local('Cancel') }}
                            </button>
                        </div>
                    </div>
                </template>

                <template v-else-if="renamingId === item.id">
                    <input
                        ref="rename"
                        v-model="draftName"
                        class="lp-input"
                        type="text"
                        @click.stop
                        @keydown.enter="confirmRename(item)"
                        @keydown.esc="renamingId = null"
                        @blur="confirmRename(item)"
                    />
                </template>

                <template v-else>
                    <div class="lp-tasks__row">
                        <span class="lp-dot" :class="dotClass(item)"></span>
                        <span class="lp-tasks__name lp-truncate" :title="item.name">{{ item.name }}</span>
                        <div class="lp-tasks__actions">
                            <button
                                type="button"
                                class="lp-btn lp-btn--ghost lp-btn--icon lp-btn--sm"
                                :title="local('Rename Task')"
                                @click.stop="startRename(item)"
                            >
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                                    <path d="M4 20h4L19 9a2.1 2.1 0 0 0-3-3L5 17v3z" />
                                </svg>
                            </button>
                            <button
                                type="button"
                                class="lp-btn lp-btn--ghost lp-btn--icon lp-btn--sm"
                                :title="local('Delete Task')"
                                @click.stop="armedId = item.id"
                            >
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
                                    <path d="M5 7h14M10 7V5h4v2M8 7l1 12h6l1-12" />
                                </svg>
                            </button>
                        </div>
                    </div>
                    <div class="lp-tasks__meta">
                        <span class="lp-truncate" :title="item.task_id">{{ item.task_id }}</span>
                        <span class="lp-tasks__age">{{ relativeTime(item.updatedAt) }}</span>
                    </div>
                </template>
            </div>

            <p v-if="!visibleTasks.length" class="lp-empty">
                {{
                    searchText
                        ? local('No task matches that.')
                        : local('No tasks yet. The first thing you send becomes one.')
                }}
            </p>
        </div>
    </aside>
</template>

<script>
import { mapState, mapActions } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'
import { useLoopAI } from '@/stores/loopAI'

export default {
    name: 'TaskColumn',
    data() {
        return {
            searchText: '',
            showSearch: false,
            creating: false,
            draftName: '',
            renamingId: null,
            armedId: null
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        ...mapState(useLoopAI, ['tasks', 'currentTask', 'msgStreamModel']),
        visibleTasks() {
            const needle = this.searchText.trim().toLowerCase()
            if (!needle) return this.tasks
            return this.tasks.filter((item) => (item.name || '').toLowerCase().includes(needle))
        }
    },
    mounted() {
        this.getTasks()
    },
    methods: {
        ...mapActions(useLoopAI, [
            'getTasks',
            'setCurrentTask',
            'createTask',
            'renameTask',
            'deleteTask'
        ]),
        isCurrent(item) {
            return this.currentTask?.task_id === item.task_id
        },
        dotClass(item) {
            if (!this.isCurrent(item)) return item.state === 'failed' ? 'is-failed' : ''
            if (this.msgStreamModel.loading) return 'is-running'
            if (this.msgStreamModel.status === 'failed') return 'is-failed'
            return 'is-ok'
        },
        select(item) {
            if (this.isCurrent(item)) return
            this.armedId = null
            this.setCurrentTask(item)
        },
        toggleSearch() {
            this.showSearch = !this.showSearch
            if (this.showSearch) {
                this.$nextTick(() => this.$refs.search?.focus())
            } else {
                this.searchText = ''
            }
        },
        closeSearch() {
            this.searchText = ''
            this.showSearch = false
        },
        startCreate() {
            this.creating = true
            this.draftName = ''
            this.$nextTick(() => this.$refs.create?.focus())
        },
        cancelCreate() {
            this.creating = false
            this.draftName = ''
        },
        async confirmCreate() {
            if (!this.creating) return
            const name = this.draftName
            this.creating = false
            this.draftName = ''
            await this.createTask(name)
        },
        startRename(item) {
            this.renamingId = item.id
            this.draftName = item.name
            this.$nextTick(() => {
                const input = Array.isArray(this.$refs.rename) ? this.$refs.rename[0] : this.$refs.rename
                input?.focus()
                input?.select()
            })
        },
        async confirmRename(item) {
            if (this.renamingId !== item.id) return
            const name = this.draftName
            this.renamingId = null
            this.draftName = ''
            if (name && name !== item.name) await this.renameTask(item, name)
        },
        async confirmDelete(item) {
            this.armedId = null
            await this.deleteTask(item)
        },
        relativeTime(value) {
            if (!value) return '—'
            const then = new Date(value).getTime()
            if (Number.isNaN(then)) return '—'
            const seconds = Math.max(0, Math.round((Date.now() - then) / 1000))
            if (seconds < 60) return this.local('now')
            const minutes = Math.round(seconds / 60)
            if (minutes < 60) return `${minutes}m`
            const hours = Math.round(minutes / 60)
            if (hours < 24) return `${hours}h`
            const days = Math.round(hours / 24)
            if (days < 30) return `${days}d`
            return new Date(value).toISOString().slice(0, 10)
        }
    }
}
</script>

<style lang="scss">
.lp-tasks {
    width: var(--lp-column);
    flex-shrink: 0;
    background: var(--lp-chrome);
    border-right: 1px solid var(--lp-line);
    display: flex;
    flex-direction: column;
    min-height: 0;

    .lp-tasks__head {
        height: var(--lp-bar);
        padding: 0 8px 0 14px;
        border-bottom: 1px solid var(--lp-line);
        display: flex;
        align-items: center;
        justify-content: space-between;
        flex-shrink: 0;
    }

    .lp-tasks__head-actions {
        gap: 4px;
        display: flex;
        align-items: center;

        .is-on {
            color: var(--lp-text);
            background: var(--lp-raised);
        }
    }

    .lp-tasks__search {
        padding: 8px 8px 4px 8px;
        flex-shrink: 0;
    }

    .lp-tasks__list {
        flex: 1;
        padding: 6px;
        gap: 2px;
        display: flex;
        flex-direction: column;
        min-height: 0;
    }

    .lp-tasks__item {
        padding: 9px 10px;
        gap: 5px;
        border-radius: var(--lp-r-2);
        border-left: 2px solid transparent;
        display: flex;
        flex-direction: column;
        cursor: pointer;
        transition: background var(--lp-fast) var(--lp-ease);

        &:hover {
            background: var(--lp-surface);

            .lp-tasks__actions {
                opacity: 1;
            }
        }

        &.is-current {
            background: var(--lp-raised);
            border-left-color: var(--lp-accent);
        }
    }

    .lp-tasks__row {
        gap: 7px;
        display: flex;
        align-items: center;
    }

    .lp-tasks__name {
        flex: 1;
        min-width: 0;
        font-size: var(--lp-t-md);
        color: var(--lp-text-dim);
    }

    .is-current .lp-tasks__name {
        color: var(--lp-text);
        font-weight: 500;
    }

    .lp-tasks__actions {
        gap: 2px;
        display: flex;
        align-items: center;
        opacity: 0;
        transition: opacity var(--lp-fast) var(--lp-ease);
    }

    .lp-tasks__meta {
        gap: 8px;
        font-family: var(--lp-mono);
        font-size: var(--lp-t-xs);
        color: var(--lp-text-mute);
        display: flex;
        align-items: center;
        justify-content: space-between;
    }

    .lp-tasks__age {
        flex-shrink: 0;
    }

    .lp-tasks__armed {
        gap: 8px;
        display: flex;
        flex-direction: column;
    }

    .lp-tasks__armed-copy {
        font-size: var(--lp-t-cap);
        color: var(--lp-text);
    }

    .lp-tasks__armed-actions {
        gap: 6px;
        display: flex;
    }
}
</style>
