<template>
    <span class="simple-text-block" :class="[{ 'text-shimmer': loading }]">
        <p class="title">{{ thisValue.title }}</p>
        <p class="content">{{ thisValue.content }}</p>
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
    }
}
</script>

<style lang="scss">
.simple-text-block {
    @include Vcenter;

    height: auto;
    gap: 5px;
    padding: 0px 5px;
    border-radius: 6px;
    opacity: 0.7;
    transition: opacity 0.3s ease-in-out;
    text-shadow: 1px 0px 3px rgba(0, 0, 0, 0.1);

    &:hover {
        opacity: 1;
    }

    .title {
        font-size: 12px;
        font-weight: 500;
        line-height: 1.5;
    }

    .content {
        @include nowrap;

        font-size: 12px;
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
