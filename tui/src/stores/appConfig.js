import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { createLocal, i18n, resolveLanguage } from '../i18n.js'

export const useAppConfig = defineStore('appConfig', () => {
  const languageValue = ref('en')
  const page = ref('home')
  const local = createLocal(() => languageValue.value)

  function setLanguageFromConfig(config) {
    languageValue.value = resolveLanguage(config)
  }

  function setPage(nextPage) {
    page.value = nextPage
  }

  return {
    i18n,
    language: computed(() => languageValue.value),
    page: computed(() => page.value),
    local,
    setLanguageFromConfig,
    setPage
  }
})
