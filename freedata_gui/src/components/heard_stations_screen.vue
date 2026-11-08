<script setup>
// Heard stations: one row per station with the latest time it was heard (in the
// browser's local time) and the frequency. Click a column header to sort by station
// or by date and time; a second click reverses the order.
import { computed, ref } from "vue";

import { setActivePinia } from "pinia";
import pinia from "../store/index";
setActivePinia(pinia);

import { useStateStore } from "../store/stateStore.js";
const state = useStateStore(pinia);

// newest first, like the heard stations widget
const sortKey = ref("timestamp");
const sortAsc = ref(false);

function sortBy(key) {
  if (sortKey.value === key) {
    sortAsc.value = !sortAsc.value;
  } else {
    sortKey.value = key;
    // stations A-Z first, times newest first
    sortAsc.value = key === "origin";
  }
}

const stations = computed(() => {
  const direction = sortAsc.value ? 1 : -1;
  return [...state.heard_stations].sort((a, b) => {
    let result;
    if (sortKey.value === "origin") {
      result = String(a.origin ?? "").localeCompare(String(b.origin ?? ""));
    } else {
      result = (a.timestamp ?? 0) - (b.timestamp ?? 0);
    }
    return result * direction;
  });
});

function sortIcon(key) {
  if (sortKey.value !== key) return "bi bi-arrow-down-up text-muted";
  if (key === "origin") {
    return sortAsc.value ? "bi bi-sort-alpha-down" : "bi bi-sort-alpha-up";
  }
  return sortAsc.value ? "bi bi-sort-numeric-down" : "bi bi-sort-numeric-up";
}

function ariaSort(key) {
  if (sortKey.value !== key) return "none";
  return sortAsc.value ? "ascending" : "descending";
}

// the server sends seconds since the epoch (UTC); show them in local time
function localDateTime(timestamp) {
  if (!timestamp) return "-";
  return new Date(timestamp * 1000).toLocaleString(navigator.language, {
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

// the server sends Hz
function frequencyKhz(frequency) {
  const hz = Number(frequency);
  if (!frequency || !Number.isFinite(hz)) return "-";
  return `${(hz / 1000).toLocaleString(navigator.language, { maximumFractionDigits: 3 })} kHz`;
}
</script>

<template>
  <div
    class="container-fluid p-3 overflow-auto"
    style="height: calc(100vh - 48px);"
  >
    <div class="d-flex align-items-center mb-1">
      <h5 class="mb-0 me-2">
        {{ $t('heardscreen.title') }}
      </h5>
      <span class="badge text-bg-secondary me-auto">{{ state.heard_stations.length }}</span>
    </div>
    <p class="text-muted small">
      {{ $t('heardscreen.description') }}
    </p>

    <div
      v-if="state.heard_stations.length === 0"
      class="text-muted"
    >
      {{ $t('heardscreen.empty') }}
    </div>

    <div
      v-else
      class="table-responsive"
    >
      <table
        id="tblHeardStationsScreen"
        class="table table-sm table-striped table-hover align-middle"
      >
        <thead>
          <tr>
            <th
              scope="col"
              :aria-sort="ariaSort('origin')"
            >
              <button
                type="button"
                class="btn btn-link btn-sm p-0 text-reset text-decoration-none fw-bold"
                :title="$t('heardscreen.sort_help')"
                @click="sortBy('origin')"
              >
                {{ $t('heardscreen.station') }}
                <i :class="sortIcon('origin')" />
              </button>
            </th>
            <th
              scope="col"
              :aria-sort="ariaSort('timestamp')"
            >
              <button
                type="button"
                class="btn btn-link btn-sm p-0 text-reset text-decoration-none fw-bold"
                :title="$t('heardscreen.sort_help')"
                @click="sortBy('timestamp')"
              >
                {{ $t('heardscreen.datetime') }}
                <i :class="sortIcon('timestamp')" />
              </button>
            </th>
            <th scope="col">
              {{ $t('heardscreen.frequency') }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="item in stations"
            :key="item.origin"
          >
            <td>
              <span class="badge text-bg-primary">{{ item.origin }}</span>
            </td>
            <td>{{ localDateTime(item.timestamp) }}</td>
            <td>{{ frequencyKhz(item.frequency) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
