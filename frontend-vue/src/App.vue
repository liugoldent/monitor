<script setup lang="ts">
import { ref } from 'vue'
import MarketTable from './components/MarketTable.vue'
import DashboardPanel from './components/DashboardPanel.vue'

type MarketItem = {
  id: string | number
  name: string
  nearMonth: number
  farMonth: number
  combine: number
}

const highest20 = ref<MarketItem[]>([])
const lowest20 = ref<MarketItem[]>([])
</script>

<template>
  <main class="h-screen w-screen overflow-hidden bg-gradient-to-br from-slate-950 via-slate-900 to-slate-800 p-4">
    <div class="grid grid-cols-2 gap-4 h-full w-full">
      <div class="h-full rounded-xl overflow-hidden shadow-2xl border border-gray-800">
        <MarketTable
          @update:highest20="highest20 = $event"
          @update:lowest20="lowest20 = $event"
        />
      </div>
      <div class="h-full rounded-xl overflow-hidden shadow-2xl border border-gray-800">
        <DashboardPanel :highest20="highest20" :lowest20="lowest20" />
      </div>
    </div>
  </main>
</template>
