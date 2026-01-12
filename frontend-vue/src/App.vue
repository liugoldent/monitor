<script setup lang="ts">
import MarketTable from './components/MarketTable.vue'
import DashboardPanel from './components/DashboardPanel.vue'
</script>

<template>
  <main class="h-screen w-screen overflow-hidden bg-gradient-to-br from-slate-950 via-slate-900 to-slate-800 p-4">
    <div class="grid grid-cols-2 gap-4 h-full w-full">
      <div class="h-full rounded-xl overflow-hidden shadow-2xl border border-gray-800">
        <MarketTable />
      </div>
      <div class="h-full rounded-xl overflow-hidden shadow-2xl border border-gray-800">
        <DashboardPanel />
      </div>
    </div>
  </main>
</template>
