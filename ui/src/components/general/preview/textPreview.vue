<template>
    <div class="text-wrapper">
        <i v-show="!content" class="empty-icon ms-Icon ms-Icon--Important"></i>
        <p v-show="!content" class="empty-title">{{ local('No Data') }}</p>
        <power-editor :theme="theme" ref="editor"
            :editorBackground="theme === 'dark' ? 'rgba(52, 64, 84, 0.3)' : 'white'" :editable="false"
            :editorOutSideBackground="theme === 'dark' ? 'rgba(52, 64, 84, 0.3)' : 'white'
                " style="width: 100%; height: 100%;" @on-mounted="setEditorContent"></power-editor>
    </div>
</template>

<script setup>
import { getCurrentInstance } from 'vue';

const { proxy } = getCurrentInstance()

defineExpose({
    getHeads: (...args) => proxy.getHeads(...args)
})
</script>

<script>
import { mapState } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'
import { useTheme } from '@/stores/theme';

export default {
    props: {
        content: {
            type: String,
            default: "Rich Content"
        }
    },
    data() {
        return {

        }
    },
    watch: {
        content() {
            this.setEditorContent()
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        ...mapState(useTheme, ['theme'])

    },
    mounted() {

    },
    methods: {
        setEditorContent() {
            let decode = this.content.replace(/\n\n/g, '\n')
            return this.$refs.editor.insertMarkdown(decode)
        }
    }
}
</script>

<style lang="scss">
.text-wrapper {
    position: relative;
    width: 100%;
    height: 500px;
    padding: 5px;
    background: white;
    border: 1px solid rgba(120, 120, 120, 0.1);
    border-radius: 8px;
    box-shadow: 0px 1px 3px rgba(0, 0, 0, 0.1);
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    overflow: hidden;

    .empty-icon {
        font-size: 40px;
        color: rgba(120, 120, 120, 0.5);
    }

    .empty-title {
        font-size: 16px;
        color: rgba(120, 120, 120, 0.5);
    }
}
</style>
