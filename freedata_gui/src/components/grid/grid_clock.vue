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
  <div class="clock-widget border rounded w-100 h-100 text-bg-light">
    <div class="clock-lines">
      <span class="clock-label text-body-secondary">{{ $t('grid.clock_local') }}</span>
      <strong class="clock-time font-monospace">{{ formatLocal(now) }}</strong>
      <span class="clock-label text-body-secondary">UTC</span>
      <strong class="clock-time font-monospace">{{ formatUtc(now) }}</strong>
    </div>
  </div>
</template>

<style scoped>
/* The widget is a size container, so the text follows the widget's size:
   resize the widget in the grid to make the clock bigger or smaller. */
.clock-widget {
  container-type: size;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}

.clock-lines {
  display: grid;
  grid-template-columns: auto auto;
  align-items: baseline;
  column-gap: 0.3em;
  line-height: 1.15;
  /* Two lines must fit the height and "Local 00:00:00" the width */
  font-size: clamp(10px, min(38cqh, 12cqw), 400px);
}

.clock-label {
  font-size: 0.7em;
  text-align: right;
}
</style>
