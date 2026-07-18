import { ref, computed } from 'vue'
import { defineStore } from 'pinia'

const LANGUAGE_STORAGE_KEY = 'loopai-language'

export function getStoredLanguage() {
    try {
        return localStorage.getItem(LANGUAGE_STORAGE_KEY)
    } catch (error) {
        return null
    }
}

export const useAppConfig = defineStore('useAppConfig', () => {
    const screenWidth = ref(999999999);
    const config = ref({
        language: getStoredLanguage() || 'en'
    })
    const i18n = ref({})

    function setScreenWidth(obj) {
        screenWidth.value = obj
    }

    function reviseI18N(val) {
        i18n.value = val
    }

    function reviseConfig(val) {
        config.value = {
            ...config.value,
            ...val
        }
    }

    function setLanguage(val) {
        reviseConfig({ language: val })
        try {
            localStorage.setItem(LANGUAGE_STORAGE_KEY, val)
        } catch (error) {}
    }

    const language = computed(() => {
        return config.value.language
    })
    const local = text => {
        return computed(() => {
            let result = i18n.value[text];
            if (!result)
                return text;
            return result[config.value.language];
        }).value
    }

    return {
        language,
        screenWidth,
        setScreenWidth,
        reviseI18N,
        reviseConfig,
        setLanguage,
        local
    }
})
