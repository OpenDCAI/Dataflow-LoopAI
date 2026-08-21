<template>
    <div id="app">
        <router-view />
    </div>
</template>

<script>
import i18n from '@/js/i18n.js'
import { mapActions, mapState } from 'pinia'
import { useAppConfig, getStoredLanguage } from '@/stores/appConfig'
import { useTheme } from '@/stores/theme'
import { useLoopAI } from '@/stores/loopAI'

export default {
    name: 'App',
    computed: {
        ...mapState(useTheme, ['theme'])
    },
    mounted() {
        this.i18nInit()
        this.syncScreenWidth()
        window.addEventListener('resize', this.syncScreenWidth)
    },
    beforeUnmount() {
        window.removeEventListener('resize', this.syncScreenWidth)
    },
    methods: {
        ...mapActions(useAppConfig, {
            reviseI18N: 'reviseI18N',
            reviseConfig: 'reviseConfig',
            setScreenWidth: 'setScreenWidth'
        }),
        ...mapActions(useLoopAI, ['getConfigs']),
        async i18nInit() {
            this.reviseI18N(i18n)
            await this.refreshLanguage()
        },
        async refreshLanguage() {
            // A language picked via the in-app switcher wins over the backend config.
            const storedLanguage = getStoredLanguage()
            if (storedLanguage) {
                this.reviseConfig({
                    language: storedLanguage
                })
                return
            }
            await this.getConfigs()
                .then((res) => {
                    let language = 'en'
                    try {
                        language = res.data.states.default.language.value
                    } catch (error) {}
                    if (!language) language = 'en'
                    this.reviseConfig({
                        language: language
                    })
                })
                .catch((error) => {
                    console.log(error)
                })
        },
        /* was a 300ms setInterval — a resize listener is the same answer for free */
        syncScreenWidth() {
            this.setScreenWidth(document.body.clientWidth)
        }
    }
}
</script>
