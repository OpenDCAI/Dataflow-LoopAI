<template>
    <span
        class="simple-text-block"
        :class="[{ 'text-shimmer': loading }]"
        :title="thisValue.title + ' ' + thisValue.content"
    >
        <i v-if="thisValue.icon" class="ms-Icon" :class="[`ms-Icon--${thisValue.icon}`]"></i>
        <p class="title">{{ thisValue.title }}</p>
        <p class="content" @click="copyTextContent(thisValue.content)">{{ thisValue.content }}</p>
    </span>
</template>

<script>
export default {
    props: {
        modelValue: {
            type: Object,
            default: () => ({})
        },
        loading: {
            type: Boolean,
            default: false
        }
    },
    data() {
        return {
            thisValue: this.modelValue
        }
    },
    watch: {
        modelValue() {
            this.thisValue = this.modelValue
        }
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
.simple-text-block {
    @include Vcenter;
    @include nowrap;

    height: auto;
    gap: 5px;
    padding: 0px 5px;
    border-radius: 6px;
    opacity: 0.7;
    transition: opacity 0.3s ease-in-out;
    text-shadow: 1px 0px 2px rgba(0, 0, 0, 0.1);

    &:hover {
        opacity: 1;
    }

    .ms-Icon {
        font-size: 12px;
    }

    .title {
        font-size: 10px;
        font-weight: 500;
        line-height: 1.5;
        cursor: default;
    }

    .content {
        @include nowrap;

        font-size: 10px;
        font-weight: 400;
        line-height: 1.5;
    }

    &.text-shimmer {
        color: transparent;
        background-image: linear-gradient(
            110deg,
            rgba(36, 36, 36, 0.8) 0%,
            rgba(36, 36, 36, 0.8) 38%,
            rgba(255, 255, 255, 0.95) 48%,
            rgba(203, 213, 225, 0.9) 53%,
            rgba(36, 36, 36, 0.8) 62%,
            rgba(36, 36, 36, 0.8) 100%
        );
        background-size: 260% 100%;
        background-position: 120% 0;

        -webkit-background-clip: text;
        background-clip: text;
        -webkit-text-fill-color: transparent;

        animation: text-shimmer 2.8s ease-in-out infinite;
    }

    @keyframes text-shimmer {
        0% {
            background-position: 120% 0;
        }
        55% {
            background-position: -40% 0;
        }
        100% {
            background-position: -120% 0;
        }
    }
}
</style>
