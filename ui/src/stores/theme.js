import { ref, computed } from 'vue'
import { defineStore } from 'pinia'

const THEME_STORAGE_KEY = 'loopai-theme'

/**
 * The console is dark by default. `data-theme` on <html> is what the token
 * sheet reads; everything visual follows from there.
 */
const ACCENT = {
    dark: '#6ba7ff',
    light: '#2f6fd0'
}

export function getStoredTheme() {
    try {
        const stored = localStorage.getItem(THEME_STORAGE_KEY)
        return stored === 'light' || stored === 'dark' ? stored : null
    } catch (error) {
        return null
    }
}

function applyTheme(value) {
    if (typeof document === 'undefined') return
    document.documentElement.setAttribute('data-theme', value)
}

export const useTheme = defineStore('useTheme', () => {
    const Theme = ref(getStoredTheme() || 'dark')
    applyTheme(Theme.value)

    function reviseTheme(themeValue) {
        if (themeValue !== 'light' && themeValue !== 'dark') return
        Theme.value = themeValue
        applyTheme(themeValue)
        try {
            localStorage.setItem(THEME_STORAGE_KEY, themeValue)
        } catch (error) {
            /* private mode — the theme just does not persist */
        }
    }

    function toggleTheme() {
        reviseTheme(Theme.value === 'dark' ? 'light' : 'dark')
    }

    const theme = computed(() => Theme.value)
    const color = computed(() => ACCENT[Theme.value])

    /**
     * `gradient` used to be a two-stop brand gradient painted over every
     * primary control. It now resolves to the flat accent so the components
     * still reading it inherit the console look without a call-site change.
     */
    const gradient = computed(() => ACCENT[Theme.value])
    const color01 = computed(() => (Theme.value === 'dark' ? 'rgba(107, 167, 255, 0.12)' : 'rgba(47, 111, 208, 0.1)'))
    const gradient01 = computed(() => color01.value)
    const gray01 = computed(() => (Theme.value === 'dark' ? '#1a1f27' : '#f1f3f6'))

    return {
        reviseTheme,
        toggleTheme,
        theme,
        color,
        color01,
        gradient,
        gradient01,
        gray01
    }
})
