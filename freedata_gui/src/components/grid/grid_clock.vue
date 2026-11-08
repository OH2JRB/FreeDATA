<script setup>
import { ref, onMounted, onBeforeUnmount } from "vue";

const now = ref(new Date());
let timer = null;

function pad(n) {
  return String(n).padStart(2, "0");
}

function formatLocal(d) {
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function formatUtc(d) {
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
}

onMounted(() => {
  timer = setInterval(() => {
    now.value = new Date();
  }, 1000);
});

onBeforeUnmount(() => {
  clearInterval(timer);
});
</script>

<template>
  <div
    class="d-flex flex-column justify-content-center align-items-center border rounded w-100 h-100 text-bg-light lh-sm"
  >
    <div :title="$t('grid.clock_local')">
      <span class="small text-body-secondary me-1">{{ $t('grid.clock_local') }}</span>
      <strong class="font-monospace">{{ formatLocal(now) }}</strong>
    </div>
    <div title="UTC">
      <span class="small text-body-secondary me-1">UTC</span>
      <strong class="font-monospace">{{ formatUtc(now) }}</strong>
    </div>
  </div>
</template>
