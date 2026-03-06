<template>
    <basePanel
        v-model="thisValue"
        :title="title"
        width="min(800px, 90%)"
        height="min(800px, 90%)"
        theme="light"
        :teleport="true"
    >
        <template v-slot:content>
            <div class="panel-dir-selector-content-block">
                <div class="dir-selector-item-main">
                    <fv-breadcrumb
                        v-model="thisFilePath"
                        :readOnly="readOnly"
                        :border-radius="6"
                        style="width: 100%; flex-shrink: 0"
                        @item-click="handleDirClick"
                        @input-change="debounceGetFiles"
                        @keydown.down="handlePathMove"
                        @keydown.up="handlePathMove"
                        @keyup.enter="thisValue = false"
                    >
                    </fv-breadcrumb>
                    <div class="dir-selector-list">
                        <fv-list-view
                            v-model="files"
                            :showSlider="false"
                            :row-height="30"
                            ref="list_view"
                            style="width: 100%; height: 100%"
                            @selection-change="changeSelection"
                            @choose-item="chooseItem"
                        >
                            <template v-slot:listItem="x">
                                <img
                                    :src="computedImg(x.item)"
                                    alt=""
                                    style="
                                        width: 15px;
                                        height: 15px;
                                        margin-right: 8px;
                                        object-fit: contain;
                                        user-select: none;
                                    "
                                />
                                <p :style="{ color: color }">
                                    {{ nameMatched(x.item) }}
                                </p>
                                <p>{{ nameNotMatched(x.item) }}</p>
                            </template>
                        </fv-list-view>
                    </div>
                </div>
            </div>
        </template>
        <template v-slot:control="{ close }">
            <fv-button
                theme="dark"
                :background="'linear-gradient(130deg, rgba(229, 123, 67, 1), rgba(252, 98, 32, 1))'"
                :border-radius="8"
                :is-box-shadow="true"
                style="width: 120px; margin-right: 10px"
                @click="handleConfirm"
                >{{ local('Confirm') }}</fv-button
            >
            <fv-button
                :borderRadius="8"
                :isBoxShadow="true"
                style="width: 120px; margin-right: 8px"
                @click="handleCancel(close)"
                >{{ local('Close') }}</fv-button
            >
        </template>
    </basePanel>
</template>

<script>
import { mapState, mapActions } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'
import { useTheme } from '@/stores/theme'

import basePanel from '@/components/general/basePanel.vue'

import folderImg from '@/assets/mime/folder.svg'
import fileImg from '@/assets/mime/file.svg'
import htmlImg from '@/assets/mime/html.svg'
import jsonImg from '@/assets/mime/json.svg'
import markdownImg from '@/assets/mime/markdown.svg'

export default {
    components: {
        basePanel
    },
    props: {
        modelValue: {
            default: false
        },
        title: {
            default: 'Select Directory'
        },
        filePath: {
            default: ''
        },
        readOnly: {
            default: false
        }
    },
    data() {
        return {
            thisValue: this.modelValue,
            thisFilePath: this.filePath,
            files: [],
            tempValue: this.filePath,
            imgs: {
                folder: folderImg,
                file: fileImg,
                html: htmlImg,
                json: jsonImg,
                markdown: markdownImg
            },
            timer: {
                debounce: null
            }
        }
    },
    watch: {
        modelValue(val) {
            this.thisValue = val
            this.debounceGetFiles(this.thisFilePath)
        },
        thisValue(val) {
            this.$emit('update:modelValue', val)
        },
        filePath(val) {
            this.thisFilePath = val
        },
        thisFilePath(val) {
            this.$emit('update:filePath', val)
            this.tempValue = val
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        ...mapState(useTheme, ['color', 'gradient']),
        computedImg() {
            return (item) => {
                if (item.is_dir) return this.imgs.folder
                let suffix = item.name.split('.').pop()
                if (suffix === 'html') return this.imgs.html
                if (suffix === 'json') return this.imgs.json
                if (suffix === 'md') return this.imgs.markdown
                return this.imgs.file
            }
        },
        tempValueSuffix() {
            if (this.tempValue.endsWith('/')) {
                return ''
            }
            return this.tempValue.split('/').pop()
        }
    },
    mounted() {},
    methods: {
        debounceGetFiles(event) {
            clearTimeout(this.timer.debounce)
            let val = this.thisFilePath
            this.tempValue = event
            if (event === '') val = '/'
            if (event.endsWith('/')) val = event
            else {
                let sep = event.split('/')
                val = sep.slice(0, sep.length - 1).join('/') + '/'
            }
            this.timer.debounce = setTimeout(() => {
                this.getFiles(val)
            }, 300)
        },
        getFiles(path) {
            this.$api.config.listDir(path).then((res) => {
                if (res.code === 200) {
                    let files = res.data || []
                    files.forEach((item) => {
                        item.key = item.name
                        item.prefix = path
                        item.nameMatched = ''
                        item.nameNotMatched = item.name
                    })
                    this.files = files
                } else this.files = []
            })
        },
        nameMatched(item) {
            if (item.name.startsWith(this.tempValueSuffix)) {
                return this.tempValueSuffix
            }
            return ''
        },
        nameNotMatched(item) {
            return item.name.slice(this.nameMatched(item).length)
        },
        handlePathMove(event) {
            if (this.readOnly) return
            if (event.key === 'ArrowUp') {
                this.$refs.list_view.move(event, -1)
            } else if (event.key === 'ArrowDown') {
                this.$refs.list_view.move(event, 1)
            }
        },
        changeSelection(event) {
            if (this.readOnly) return
            this.thisFilePath = event.prefix + event.name
            this.$nextTick(() => {
                clearTimeout(this.timer.debounce)
            })
        },
        chooseItem({ item }) {
            if (this.readOnly) return
            if (item.is_dir) {
                this.thisFilePath = item.prefix + item.name + '/'
                this.getFiles(this.thisFilePath)
            }
        },
        handleConfirm() {
            this.thisValue = false
        },
        handleDirClick(item) {
            if (this.readOnly) return
            this.getFiles(item.fullPath)
            this.thisFilePath = item.fullPath
        },
        handleCancel(close) {
            this.$emit('cancel')
            close()
        }
    }
}
</script>

<style lang="scss">
.panel-dir-selector-content-block {
    position: relative;
    width: 100%;
    height: 100%;
    gap: 5px;
    display: flex;
    flex-direction: column;
    overflow: overlay;

    .dir-selector-item-main {
        position: relative;
        width: 100%;
        flex: 1;
        height: 100%;
        padding: 5px;
        display: flex;
        align-items: center;
        flex-direction: column;

        .main-icon {
            @include HcenterVcenter;

            position: relative;
            width: 40px;
            height: 40px;
            flex-shrink: 0;
            background: linear-gradient(
                90deg,
                rgba(73, 131, 251, 1) 0%,
                rgba(100, 161, 252, 1) 100%
            );
            border: 1px solid rgba(120, 120, 120, 0.1);
            border-radius: 8px;
            color: whitesmoke;
            box-shadow: 0px 1px 2px rgba(0, 0, 0, 0.1);
        }

        .content-block {
            @include HstartC;

            position: relative;
            width: 50px;
            flex: 1;
            height: 100%;
            padding: 10px;
            line-height: 2;
            user-select: none;
        }

        .dir-selector-list {
            position: relative;
            width: 100%;
            flex: 1;
            padding: 15px 0px;
            overflow: hidden;
        }
    }
}
</style>
