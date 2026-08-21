<template>
    <div class="lp-shell">
        <nav class="lp-rail">
            <router-link to="/m" class="lp-rail__mark" :title="local('Task Hub')">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
                    <path d="M4 12a8 8 0 1 0 3-6.2" />
                    <path d="M4 4v4h4" />
                </svg>
            </router-link>

            <button
                v-for="item in navList"
                :key="item.key"
                type="button"
                class="lp-rail__item"
                :class="{ 'is-active': item.key === activeKey }"
                :title="local(item.name)"
                :aria-label="local(item.name)"
                :aria-current="item.key === activeKey ? 'page' : null"
                @click="$Go(item.route)"
            >
                <span class="lp-rail__icon" v-html="item.icon"></span>
            </button>

            <div class="lp-rail__spacer"></div>

            <button
                type="button"
                class="lp-rail__item"
                :title="local(theme === 'dark' ? 'Light theme' : 'Dark theme')"
                @click="toggleTheme"
            >
                <span class="lp-rail__icon">
                    <svg v-if="theme === 'dark'" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round">
                        <circle cx="12" cy="12" r="4" />
                        <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
                    </svg>
                    <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round">
                        <path d="M20 13.5A8 8 0 0 1 10.5 4a8.2 8.2 0 1 0 9.5 9.5z" />
                    </svg>
                </span>
            </button>

            <button
                type="button"
                class="lp-rail__item lp-rail__item--text"
                :title="local('Switch Language')"
                @click="setLanguage(language === 'zh' ? 'en' : 'zh')"
            >
                {{ language === 'zh' ? '中' : 'EN' }}
            </button>
        </nav>

        <main class="lp-shell__view">
            <router-view v-slot="{ Component }">
                <KeepAlive>
                    <component :is="Component" />
                </KeepAlive>
            </router-view>
        </main>
    </div>
</template>

<script>
import { mapState, mapActions } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'
import { useTheme } from '@/stores/theme'

const ICON_WORKSPACE = `<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>`
const ICON_CONFIG = `<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/></svg>`
const ICON_LAKE = `<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><ellipse cx="12" cy="5.5" rx="7.5" ry="2.8"/><path d="M4.5 5.5v13c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8v-13"/><path d="M4.5 12c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8"/></svg>`

export default {
    name: 'ConsoleShell',
    data() {
        return {
            navList: [
                { key: 'workspace', name: 'Task Hub', route: '/m', icon: ICON_WORKSPACE },
                { key: 'config', name: 'Config', route: '/m/config', icon: ICON_CONFIG },
                { key: 'datamixer', name: 'DataMixer', route: '/m/datamixer', icon: ICON_LAKE }
            ]
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local', 'language']),
        ...mapState(useTheme, ['theme']),
        activeKey() {
            const path = this.$route.path
            if (path.startsWith('/m/config')) return 'config'
            if (path.startsWith('/m/datamixer') || path.startsWith('/m/obtainer-lake')) return 'datamixer'
            return 'workspace'
        }
    },
    methods: {
        ...mapActions(useAppConfig, ['setLanguage']),
        ...mapActions(useTheme, ['toggleTheme'])
    }
}
</script>

<style lang="scss">
.lp-shell {
    position: absolute;
    width: 100%;
    height: 100%;
    background: var(--lp-bg);
    display: flex;
    overflow: hidden;

    .lp-shell__view {
        position: relative;
        flex: 1;
        min-width: 0;
        display: flex;
        overflow: hidden;
    }
}

.lp-rail {
    width: var(--lp-rail);
    flex-shrink: 0;
    padding: 12px 0;
    gap: 4px;
    background: var(--lp-chrome);
    border-right: 1px solid var(--lp-line);
    display: flex;
    flex-direction: column;
    align-items: center;
    z-index: 2;

    .lp-rail__mark {
        width: 26px;
        height: 26px;
        margin-bottom: 12px;
        color: var(--lp-accent);
        border: 1px solid var(--lp-line-hi);
        border-radius: 7px;
        display: flex;
        align-items: center;
        justify-content: center;
        flex-shrink: 0;
    }

    .lp-rail__spacer {
        flex: 1;
    }

    .lp-rail__item {
        width: 34px;
        height: 34px;
        color: var(--lp-text-mute);
        border-radius: var(--lp-r-3);
        flex-shrink: 0;
        display: flex;
        align-items: center;
        justify-content: center;
        transition:
            background var(--lp-fast) var(--lp-ease),
            color var(--lp-fast) var(--lp-ease);

        &:hover {
            background: var(--lp-raised);
            color: var(--lp-text);
        }

        &.is-active {
            background: var(--lp-accent-wash);
            color: var(--lp-accent);
        }

        &.lp-rail__item--text {
            font-family: var(--lp-mono);
            font-size: var(--lp-t-sm);
        }
    }

    .lp-rail__icon {
        display: flex;
        align-items: center;
        justify-content: center;
    }
}
</style>
