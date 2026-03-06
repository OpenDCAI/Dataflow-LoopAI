<template>
    <div class="table-wrapper">
        <i v-show="!tableInfo.length" class="empty-icon ms-Icon ms-Icon--Important"></i>
        <p v-show="!tableInfo.length" class="empty-title">{{ local('No Data') }}</p>
        <fv-details-list v-show="tableInfo.length" :model-value="tableInfo" :head="heads" ref="table"
            style="width: 100%; height: 100%">
            <template v-for="(col, i) in heads" :key="i + 1" v-slot:[`column_${i}`]="x">
                <p :title="i == 0 ? x.row_index + 1 : x.item[col.key] ? x.item[col.key] : ''">
                    {{ i == 0 ? x.row_index + 1 : x.item[col.key] ? x.item[col.key] : '' }}
                </p>
            </template>
        </fv-details-list>
    </div>
    <fv-pagination v-show="pages > 0 && tableInfo.length" v-model="thisCurrentPage" :total="pages"
        background="rgba(255, 255, 255, 1)" foreground="rgba(111, 92, 196, 1)" :small="true"
        style="width: 100%; height: 35px; margin-top: 5px" />
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

export default {
    props: {
        tableInfo: {
            type: Array,
            default: () => []
        },
        total: {
            type: Number,
            default: 1
        },
        num_per_page: {
            type: Number,
            default: 10
        },
        currentPage: {
            type: Number,
            default: 1
        }
    },
    data() {
        return {
            thisCurrentPage: 1,
            heads: [],
        }
    },
    watch: {
        currentPage() {
            this.thisCurrentPage = this.currentPage
        },
        thisCurrentPage() {
            this.$emit('update:currentPage', this.thisCurrentPage)
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        pages() {
            return Math.ceil(this.total / this.num_per_page)
        }
    },
    mounted() {
    },
    methods: {
        getHeads() {
            if (!this.tableInfo.length) return []
            let heads = []
            heads.push({
                key: 'index',
                content: '#',
                minWidth: 60,
                width: 60
            })
            for (let key in this.tableInfo[0]) {
                heads.push({
                    key,
                    content: key,
                    minWidth: 80,
                    width: 150,
                    sortName: key
                })
            }
            heads[heads.length - 1].width = 900
            this.heads = heads
            this.$nextTick(() => {
                this.$refs.table.headInit()
            })
        }
    }
}
</script>

<style lang="scss">
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
