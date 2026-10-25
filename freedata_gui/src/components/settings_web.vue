<script setup>

import { setActivePinia } from "pinia";
import pinia from "../store/index";
setActivePinia(pinia);

import { settingsStore as settings, onChange } from "../store/settingsStore.js";

import { computed, onMounted } from "vue";
import { useEncryptionStore } from "../store/encryptionStore.js";
import { loadPSKReporterStatus, switchPSKReporter } from "../js/encryptionHandler.js";

const encryption = useEncryptionStore(pinia);

// runtime state on the server, not part of the saved config
const pskreporterEnabled = computed({
  get: () => encryption.pskreporter?.enabled === true,
  set: (enabled) => switchPSKReporter(enabled),
});

// encryption is on (for a station or with the common key): the server keeps
// Explorer publishing and PSK Reporter off and refuses to switch them on
const blockedByEncryption = computed(() => encryption.pskreporter?.blocked_by_encryption === true);

onMounted(loadPSKReporterStatus);
</script>

<template>
  <!-- Top Info Area for Explorer Settings -->
  <div
    class="alert alert-info"
    role="alert"
  >
    <strong><i class="bi bi-gear-wide-connected me-1" /></strong>{{ $t('settings.web.introduction') }}
  </div>

  <div
    class="alert alert-light"
    role="alert"
  >
    {{ $t('settings.web.description') }}
  </div>

  <div
    v-if="blockedByEncryption"
    class="alert alert-warning py-2"
    role="alert"
  >
    <i class="bi bi-lock-fill me-1" />{{ $t('settings.web.blockedbyencryption') }}
  </div>

  <!-- Explorer Publishing -->
  <div class="input-group input-group-sm mb-1">
    <label class="input-group-text w-50 text-wrap">
      {{ $t('settings.web.enableexplorer') }}
      <button
        type="button"
        class="btn btn-link p-0 ms-2"
        data-bs-toggle="tooltip"
        :title="$t('settings.web.enableexplorer_help')"
      >
        <i class="bi bi-question-circle" />
      </button>
    </label>
    <label class="input-group-text w-50">
      <div class="form-check form-switch form-check-inline">
        <input
          id="ExplorerSwitch"
          v-model="settings.remote.STATION.enable_explorer"
          class="form-check-input"
          type="checkbox"
          :disabled="blockedByEncryption"
          @change="onChange"
        >
        <label
          class="form-check-label"
          for="ExplorerSwitch"
        >{{ $t('settings.enable') }}</label>
      </div>
    </label>
  </div>

  <!-- PSK Reporter -->
  <div class="input-group input-group-sm mb-1">
    <label class="input-group-text w-50 text-wrap">
      {{ $t('settings.web.enablepskreporter') }}
      <button
        type="button"
        class="btn btn-link p-0 ms-2"
        data-bs-toggle="tooltip"
        :title="$t('settings.web.enablepskreporter_help')"
      >
        <i class="bi bi-question-circle" />
      </button>
    </label>
    <label class="input-group-text w-50">
      <div class="form-check form-switch form-check-inline">
        <input
          id="PSKReporterSwitch"
          v-model="pskreporterEnabled"
          class="form-check-input"
          type="checkbox"
          :disabled="blockedByEncryption"
        >
        <label
          class="form-check-label"
          for="PSKReporterSwitch"
        >{{ $t('settings.enable') }}</label>
      </div>
    </label>
  </div>

</template>


