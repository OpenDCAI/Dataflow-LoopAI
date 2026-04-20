<template>
    <div class="collapse-item-content">
        <hr />
        <div class="bp-row column">
            <p class="bp-title">{{ appConfig.local('Path') }}</p>
            <p class="bp-bold-info" @click="copyText(item.path)">{{ item.path }}</p>
        </div>
        <hr />
        <div class="bp-row column">
            <p class="bp-light-title">{{ appConfig.local('description') }}</p>
            <p class="bp-std-info">{{ item.description }}</p>
        </div>
        <hr />
        <div class="bp-row column">
            <p class="bp-light-title">{{ appConfig.local('Type') }}</p>
            <p class="bp-std-info">{{ item.file_type }}</p>
        </div>
        <hr />
        <div class="bp-row column">
            <p class="bp-light-title">{{ appConfig.local('Status') }}</p>
            <p class="bp-std-info">{{ item.status }}</p>
        </div>
        <hr />
        <div class="bp-row column">
            <p class="bp-light-title">{{ appConfig.local('Created At') }}</p>
            <p class="bp-std-info">
                <timeRounder :model-value="new Date(item.createdAt)" style="width: 100%" />
            </p>
        </div>
        <hr />
        <div class="bp-row column">
            <p class="bp-light-title">{{ appConfig.local('Updated At') }}</p>
            <p class="bp-std-info">
                <timeRounder :model-value="new Date(item.updatedAt)" style="width: 100%" />
            </p>
        </div>
    </div>
</template>

<script setup>
import { useAppConfig } from '@/stores/appConfig'
import { getCurrentInstance } from 'vue'

import timeRounder from '@/components/general/timeRounder.vue'

const proxy = getCurrentInstance().proxy

const appConfig = useAppConfig()

const props = defineProps({ item: { type: Object, default: () => ({}) } })

const copyText = (text) => {
    navigator.clipboard.writeText(text).then(() => {
        proxy.$barWarning(appConfig.local('Copied'), { status: 'correct' })
    })
}
</script>
