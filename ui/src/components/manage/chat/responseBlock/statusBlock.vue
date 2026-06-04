<template>
    <div class="status-block">
        <div class="row-block">
            <div class="status-role-block" :style="{ background: 'rgba(245, 245, 245, 1)' }">
                <img class="agent-logo" :src="img.tool" draggable="false" alt="" />
            </div>
            <p class="status-role-text">{{ local('Info') }}</p>
        </div>
        <div class="tool-msg-info">
            <span
                v-for="(item, index) in modelValue.content"
                class="tool-msg-item"
                :key="index"
                @click="copyTextContent(item.value)"
            >
                <p class="tool-msg-key">{{ item.key }}</p>
                <p class="tool-msg-value" :title="item.value">{{ item.value }}</p>
            </span>
        </div>
    </div>
</template>

<script>
import { useAppConfig } from '@/stores/appConfig.js'
import { mapState } from 'pinia'

import toolImg from '@/assets/chat/tool.svg'

export default {
    props: {
        modelValue: {
            type: Object,
            default: () => ({})
        }
    },
    data() {
        return {
            img: {
                tool: toolImg
            }
        }
    },
    watch: {},
    computed: {
        ...mapState(useAppConfig, ['local'])
    },
    methods: {
        copyTextContent(text) {
            if (typeof text === 'object') text = JSON.stringify(text)
            navigator.clipboard.writeText(text).then(() => {
                this.$barWarning(this.local('Copied'), {
                    status: 'correct'
                })
            })
        }
    }
}
</script>

<style lang="scss">
.status-block {
    position: relative;
    width: 100%;
    max-width: 900px;
    height: auto;
    gap: 5px;
    flex-shrink: 0;
    padding: 5px 15px;
    background: rgba(255, 255, 255, 0.3);
    border: rgba(160, 160, 160, 0.2) solid thin;
    border-radius: 12px;
    display: flex;
    flex-direction: column;

    .row-block {
        @include Vcenter;

        gap: 5px;

        .status-role-text {
            font-size: 12px;
            user-select: none;
        }

        .status-role-block {
            @include HcenterVcenter;

            width: 25px;
            height: 25px;
            border-radius: 50%;
            user-select: none;
            overflow: hidden;

            .agent-logo {
                width: 12px;
                height: 12px;
            }
        }
    }

    .tool-msg-info {
        @include Vcenter;

        width: 100%;
        max-width: 100%;
        gap: 5px;
        flex: 1;
        flex-wrap: wrap;
        flex-direction: column;
        user-select: none;
        cursor: default;

        .tool-msg-item {
            @include HbetweenVcenter;

            width: 100%;
            max-width: 100%;
            height: auto;
            gap: 5px;
            padding: 2px 5px;
            background: linear-gradient(128deg, rgba(167, 151, 238, 1), rgba(177, 168, 245, 1));
            font-size: 10px;
            color: whitesmoke;
            font-weight: bold;
            border-radius: 999px;
            box-shadow: 0px 1px 1px rgba(0, 0, 0, 0.1);

            .tool-msg-key {
                @include Vcenter;

                height: 100%;
            }

            .tool-msg-value {
                @include nowrap;
                @include Vcenter;

                width: auto;
                min-height: 15px;
                flex: 1;
                height: auto;
                flex-shrink: 0;
                padding: 5px 10px;
                background: rgba(255, 255, 255, 1);
                font-size: 10px;
                color: rgba(95, 75, 189, 1);
                border-radius: 999px;
                white-space: pre-wrap;
                text-overflow: ellipsis;
                overflow-x: overlay;
            }
        }
    }
}
</style>
