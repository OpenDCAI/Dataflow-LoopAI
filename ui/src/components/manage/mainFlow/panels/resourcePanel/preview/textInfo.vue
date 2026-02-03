<template>
    <div class="collapse-item-content">
        <div class="control-block">
            <fv-button background="transparent" border-radius="8" style="width: 30px; height: 30px"
                @click="$emit('back')">
                <i class="ms-Icon ms-Icon--Back"></i>
            </fv-button>
            <p>{{ local('Back') }}</p>
        </div>
        <text-preview :content="txtInfo" />
    </div>
</template>

<script>
import { mapState } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'
import textPreview from '@/components/general/preview/textPreview.vue';

export default {
    components: {
        textPreview
    },
    props: {
        item: {
            type: Object,
            default: () => ({})
        }
    },
    data() {
        return {
            total: 1,
            num_per_page: 30,
            currentPage: 1,
            txtInfo: ''
        }
    },
    watch: {
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
    },
    mounted() {
        this.getData()
    },
    methods: {
        getData() {
            if (!this.item.id) return
            this.$api.resource
                .previewResource(
                    this.item.id,
                    (this.currentPage - 1) * this.num_per_page,
                    this.num_per_page
                )
                .then((res) => {
                    if (res.code === 200) {
                        const { samples, count } = res.data;
                        this.txtInfo = samples.join('\n')
                        this.total = count
                    }
                })
        }
    }
}
</script>

<style lang="scss">
.control-block {
    position: relative;
    width: 100%;
    height: 35px;
    padding: 5px;
    gap: 5px;
    font-size: 13.8px;
    border-radius: 8px;
    display: flex;
    align-items: center;
    margin-bottom: 10px;
}

.table-wrapper {
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
