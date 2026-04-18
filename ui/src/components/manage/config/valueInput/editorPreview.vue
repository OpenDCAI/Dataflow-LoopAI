<template>
    <div class="value-input-editor">
        <textarea
            v-model="textModel"
            class="value-input-editor-textarea"
            :disabled="!lock"
        ></textarea>
    </div>
</template>

<script>
export default {
    props: {
        modelValue: {
            default: ''
        },
        language: {
            default: 'plaintext'
        },
        lock: {
            default: true
        }
    },
    emits: ['update:modelValue'],
    data() {
        return {
            thisValue: this.normalizeValue(this.modelValue)
        }
    },
    watch: {
        modelValue(value) {
            const next = this.normalizeValue(value)
            if (next === this.thisValue) return
            this.thisValue = next
        },
        thisValue(value) {
            this.$emit('update:modelValue', value)
        }
    },
    methods: {
        normalizeValue(value) {
            if (value === null || value === undefined) return ''
            if (typeof value === 'string') return value
            if (typeof value === 'number' || typeof value === 'boolean') return String(value)
            try {
                return JSON.stringify(value, null, 2)
            } catch (error) {
                return String(value)
            }
        }
    },
    computed: {
        textModel: {
            get() {
                return this.thisValue
            },
            set(value) {
                this.thisValue = this.normalizeValue(value)
            }
        }
    }
}
</script>

<style lang="scss">
.value-input-editor {
    width: 100%;
}

.value-input-editor-textarea {
    width: 100%;
    min-height: 120px;
    height: 160px;
    padding: 8px 10px;
    font-size: 12px;
    line-height: 1.5;
    font-family: Consolas, Monaco, 'Andale Mono', 'Ubuntu Mono', monospace;
    color: rgba(60, 60, 60, 1);
    border: 2px solid rgba(120, 120, 120, 0.15);
    border-radius: 6px;
    resize: vertical;
    outline: none;
    background: rgba(255, 255, 255, 0.9);
    box-sizing: border-box;
}

.value-input-editor-textarea:focus {
    border-color: rgba(80, 120, 220, 0.6);
}

.value-input-editor-textarea:disabled {
    cursor: not-allowed;
    opacity: 0.8;
}
</style>
