<template>
    <transition name="fade-in">
        <div
            v-show="modelValue"
            class="lp-page-loading"
            :style="{ zIndex: zIndex, backdropFilter: acrylic ? 'blur(10px)' : '' }"
        >
            <slot>
                <fv-progress-ring
                    loading="true"
                    background="rgba(245, 245, 245, 0.8)"
                    :color="color"
                ></fv-progress-ring>
                <div class="lp-page-loading-title">{{ title }}</div>
            </slot>
        </div>
    </transition>
</template>

<script>
import { mapState } from 'pinia'
import { useTheme } from '@/stores/theme'

export default {
    props: {
        modelValue: {
            type: Boolean,
            default: false
        },
        title: {
            type: String,
            default: ''
        },
        acrylic: {
            type: Boolean,
            default: false
        },
        zIndex: {
            type: Number,
            default: 9999
        }
    },
    data() {
        return {
            thisValue: this.modelValue
        }
    },
    watch: {
        modelValue(newVal, oldVal) {
            this.thisValue = newVal
        },
        thisValue(newVal, oldVal) {
            this.$emit('update:modelValue', newVal)
        }
    },
    computed: {
        ...mapState(useTheme, ['color'])
    }
}
</script>

<style lang="scss">
.lp-page-loading {
    @include HcenterVcenterC;

    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    background-color: rgba(255, 255, 255, 0.8);
    z-index: 9999;

    .lp-page-loading-title {
        margin-top: 10px;
        font-size: 16px;
        font-weight: bold;
        color: #333;
        user-select: none;
    }
}
.fade-in-enter-active,
.fade-in-leave-active {
    transition: all 0.1s ease-out;
}
.fade-in-enter-from,
.fade-in-leave-to {
    opacity: 0;
}
</style>
