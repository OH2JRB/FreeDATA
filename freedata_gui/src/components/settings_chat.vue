<script>
import { settingsStore as settings, onChange, getRemote } from "../store/settingsStore.js";
import { setActivePinia } from "pinia";
import pinia from "../store/index";
import { useEncryptionStore } from "../store/encryptionStore.js";
import {
  loadCommonKeyStatus,
  enterCommonKey,
  forgetCommonKey,
  commonKeySettingChanged,
  generateEncryptionKey,
} from "../js/encryptionHandler";

// Set the active Pinia store
setActivePinia(pinia);
const encryption = useEncryptionStore(pinia);

// Export methods and computed properties for use in the template
export default {
  data() {
    return {
      showWavelogApiKey: false,
      commonKeyInput: "",
      showCommonKey: false,
      // legal confirmation shown when the common key switch is turned on
      showCommonLegal: false,
      commonKeyError: "",
      commonKeyNotice: [],
      commonKeyBusy: false,
    };
  },
  computed: {
    settings() {
      return settings;
    },
    commonKey() {
      return encryption.common;
    },
  },
  created() {
    // Not reactive on purpose: values remembered on focus of number inputs
    this.previousNumbers = {};
    loadCommonKeyStatus();
  },
  methods: {
    onChange,
    async onCommonKeyToggle() {
      if (settings.remote.MESSAGES.enable_common_key && !this.showCommonLegal) {
        // switch it on only after the legal confirmation
        settings.remote.MESSAGES.enable_common_key = false;
        this.showCommonLegal = true;
        return;
      }
      this.showCommonLegal = false;
      await onChange();
      await commonKeySettingChanged();
    },
    confirmCommonKeyLegal() {
      settings.remote.MESSAGES.enable_common_key = true;
      this.onCommonKeyToggle();
    },
    generateCommonKey() {
      this.commonKeyInput = generateEncryptionKey();
      this.showCommonKey = true;
    },
    async runCommonKeyAction(action, notice) {
      this.commonKeyBusy = true;
      this.commonKeyError = "";
      this.commonKeyNotice = [];
      try {
        const result = await action();
        if (!result.ok) {
          this.commonKeyError = result.error;
          return;
        }
        this.commonKeyInput = "";
        this.showCommonKey = false;
        this.commonKeyNotice = notice(result.data);
      } finally {
        this.commonKeyBusy = false;
      }
    },
    saveCommonKey() {
      this.runCommonKeyAction(
        () => enterCommonKey(this.commonKeyInput),
        (data) => {
          // notice lines are [translation key, parameters]
          const lines = [["chat.encryption.keysaved", {}]];
          if (data?.decrypted) lines.push(["chat.encryption.decrypted", { n: data.decrypted }]);
          if (data?.requeued) lines.push(["chat.encryption.requeued", { n: data.requeued }]);
          return lines;
        },
      );
    },
    forgetCommonKey() {
      this.runCommonKeyAction(forgetCommonKey, () => [["chat.encryption.keyforgotten", {}]]);
    },
    rememberNumber(section, key) {
      this.previousNumbers[`${section}.${key}`] = settings.remote[section][key];
    },
    // Number inputs: v-model.number yields "" when a field is cleared, which the
    // server rejects (it expects an int). Restore the previous value instead of
    // saving, round to an integer and clamp to the input's min/max before saving.
    onNumberChange(event, section, key) {
      const el = event.target;
      let value = settings.remote[section][key];
      if (typeof value !== "number" || !Number.isFinite(value)) {
        const previous = this.previousNumbers[`${section}.${key}`];
        if (previous !== "" && previous !== null && previous !== undefined && Number.isFinite(Number(previous))) {
          settings.remote[section][key] = Number(previous);
        } else {
          getRemote(); // reload the last saved config from the server
        }
        return;
      }
      value = Math.round(value);
      if (el.min !== "" && value < Number(el.min)) value = Number(el.min);
      if (el.max !== "" && value > Number(el.max)) value = Number(el.max);
      settings.remote[section][key] = value;
      this.previousNumbers[`${section}.${key}`] = value;
      onChange();
    },
  },
};
</script>

<template>
  <!-- Top Info Area for Messages Settings -->
  <div
    class="alert alert-info"
    role="alert"
  >
    <strong><i class="bi bi-gear-wide-connected me-1" /></strong> {{ $t('settings.chat.introduction') }}
  </div>

  <!-- Enable Message Auto Repeat -->
  <div class="input-group input-group-sm mb-1">
    <label class="input-group-text w-50 text-wrap">
      {{ $t('settings.chat.enablemessageautorepeat') }}
      <button
        type="button"
        class="btn btn-link p-0 ms-2"
        data-bs-toggle="tooltip"
        :title="$t('settings.chat.enablemessageautorepeat_help')"
      >
        <i class="bi bi-question-circle" />
      </button>
    </label>
    <label class="input-group-text w-50">
      <div class="form-check form-switch form-check-inline ms-2">
        <input
          id="enableMessagesAutoRepeatSwitch"
          v-model="settings.remote.MESSAGES.enable_auto_repeat"
          class="form-check-input"
          type="checkbox"
          @change="onChange"
        >
        <label
          class="form-check-label"
          for="enableMessagesAutoRepeatSwitch"
        >{{ $t('settings.enable') }}</label>
      </div>
    </label>
  </div>

  <!-- Common encryption key for all stations -->
  <div class="input-group input-group-sm mb-1">
    <label class="input-group-text w-50 text-wrap">
      {{ $t('settings.chat.enablecommonkey') }}
      <button
        type="button"
        class="btn btn-link p-0 ms-2"
        data-bs-toggle="tooltip"
        :title="$t('settings.chat.enablecommonkey_help')"
      >
        <i class="bi bi-question-circle" />
      </button>
    </label>
    <label class="input-group-text w-50">
      <div class="form-check form-switch form-check-inline ms-2">
        <!-- switching on asks for the legal confirmation below first -->
        <input
          id="enableCommonKeySwitch"
          v-model="settings.remote.MESSAGES.enable_common_key"
          class="form-check-input"
          type="checkbox"
          @change="onCommonKeyToggle"
        >
        <label
          class="form-check-label"
          for="enableCommonKeySwitch"
        >{{ $t('settings.enable') }}</label>
      </div>
    </label>
  </div>

  <div
    v-if="showCommonLegal"
    class="alert alert-danger py-2 mb-1"
  >
    <strong><i class="bi bi-exclamation-triangle-fill me-1" />{{ $t('chat.encryption.legal_title') }}</strong>
    <p class="mb-2 mt-1 small">
      {{ $t('chat.encryption.legal_text') }}
    </p>
    <div class="form-check">
      <input
        id="commonKeyLegalConfirm"
        class="form-check-input"
        type="checkbox"
        @change="confirmCommonKeyLegal"
      >
      <label
        class="form-check-label small"
        for="commonKeyLegalConfirm"
      >
        {{ $t('chat.encryption.legal_confirm') }}
      </label>
    </div>
  </div>

  <template v-if="settings.remote.MESSAGES.enable_common_key">
    <div class="input-group input-group-sm mb-1">
      <label class="input-group-text w-50 text-wrap">
        {{ $t('settings.chat.commonkey') }}
      </label>
      <span
        v-if="commonKey?.key_loaded"
        class="input-group-text w-50 font-monospace"
        :title="$t('chat.encryption.fingerprint_help')"
      >
        <i class="bi bi-fingerprint me-1" /> {{ commonKey.fingerprint }}
      </span>
      <span
        v-else
        class="input-group-text w-50"
      >
        <span class="badge text-bg-warning">{{ $t('chat.encryption.nokey') }}</span>
      </span>
    </div>

    <div class="input-group input-group-sm mb-1">
      <input
        id="commonKeyInput"
        v-model="commonKeyInput"
        :type="showCommonKey ? 'text' : 'password'"
        class="form-control font-monospace"
        autocomplete="off"
        spellcheck="false"
        :placeholder="$t('chat.encryption.keyplaceholder')"
        @keyup.enter="commonKeyInput && saveCommonKey()"
      >
      <button
        class="btn btn-outline-secondary"
        type="button"
        :title="$t('chat.encryption.showkey')"
        @click="showCommonKey = !showCommonKey"
      >
        <i :class="showCommonKey ? 'bi bi-eye-slash' : 'bi bi-eye'" />
      </button>
      <button
        class="btn btn-outline-secondary"
        type="button"
        @click="generateCommonKey"
      >
        <i class="bi bi-shuffle me-1" />{{ $t('chat.encryption.generate') }}
      </button>
      <button
        class="btn btn-primary"
        type="button"
        :disabled="commonKeyBusy || !commonKeyInput"
        @click="saveCommonKey"
      >
        {{ $t('chat.encryption.savekey') }}
      </button>
      <button
        v-if="commonKey?.key_loaded"
        class="btn btn-outline-secondary"
        type="button"
        :disabled="commonKeyBusy"
        @click="forgetCommonKey"
      >
        {{ $t('chat.encryption.forgetkey') }}
      </button>
    </div>
    <div class="form-text mb-1">
      {{ $t('settings.chat.commonkey_help') }}
    </div>
    <div
      v-if="commonKeyError"
      class="alert alert-danger py-2 mb-1"
    >
      {{ commonKeyError }}
    </div>
    <div
      v-if="commonKeyNotice.length"
      class="alert alert-success py-2 mb-1"
    >
      <span
        v-for="[line, params] in commonKeyNotice"
        :key="line"
        class="d-block"
      >{{ $t(line, params) }}</span>
    </div>
  </template>

  <!-- Enable ADIF export -->
  <div class="input-group input-group-sm mb-1">
    <label class="input-group-text w-50 text-wrap">
      {{ $t('settings.chat.enableadifloggingudp') }}
      <button
        type="button"
        class="btn btn-link p-0 ms-2"
        data-bs-toggle="tooltip"
        :title="$t('settings.chat.enableadifloggingudp_help')"
      >
        <i class="bi bi-question-circle" />
      </button>
    </label>
    <label class="input-group-text w-50">
      <div class="form-check form-switch form-check-inline ms-2">
        <input
          id="enableADIFSwitch"
          v-model="settings.remote.QSO_LOGGING.enable_adif_udp"
          class="form-check-input"
          type="checkbox"
          @change="onChange"
        >
        <label
          class="form-check-label"
          for="enableADIFSwitch"
        >{{ $t('settings.enable') }}</label>
      </div>
    </label>
  </div>

  <!-- ADIF Log Host -->
  <div class="input-group input-group-sm mb-1">
    <label class="input-group-text w-50 text-wrap">
      {{ $t('settings.chat.adifloggingudphost') }}
      <button
        type="button"
        class="btn btn-link p-0 ms-2"
        data-bs-toggle="tooltip"
        :title="$t('settings.chat.adifloggingudphost_help')"
      >
        <i class="bi bi-question-circle" />
      </button>
    </label>
    <input
      id="adifLogHost"
      v-model="settings.remote.QSO_LOGGING.adif_udp_host"
      type="text"
      class="form-control"
      :placeholder="$t('settings.chat.adifloggingudphost_placeholder')"
      @change="onChange"
    >
  </div>

  <!-- ADIF Log Port -->
  <div class="input-group input-group-sm mb-1">
    <label class="input-group-text w-50 text-wrap">
      {{ $t('settings.chat.adifloggingudpport') }}
      <button
        type="button"
        class="btn btn-link p-0 ms-2"
        data-bs-toggle="tooltip"
        :title=" $t('settings.chat.adifloggingudpport_help')"
      >
        <i class="bi bi-question-circle" />
      </button>
    </label>
    <input
      id="adifLogPort"
      v-model.number="settings.remote.QSO_LOGGING.adif_udp_port"
      type="number"
      class="form-control"
      :placeholder=" $t('settings.chat.adifloggingudpport_placeholder')"
      max="65534"
      min="1025"
      @focus="rememberNumber('QSO_LOGGING', 'adif_udp_port')"
      @change="onNumberChange($event, 'QSO_LOGGING', 'adif_udp_port')"
    >
  </div>

  <!-- Enable Wavelog API -->
  <div class="input-group input-group-sm mb-1">
    <label class="input-group-text w-50 text-wrap">

      {{ $t('settings.chat.adifloggingwavelog') }}
      <button
        type="button"
        class="btn btn-link p-0 ms-2"
        data-bs-toggle="tooltip"
        :title="$t('settings.chat.enableadifloggingwavelog_help')"
      >
        <i class="bi bi-question-circle" />
      </button>
    </label>
    <label class="input-group-text w-50">
      <div class="form-check form-switch form-check-inline ms-2">
        <input
          id="enableWavelogSwitch"
          v-model="settings.remote.QSO_LOGGING.enable_adif_wavelog"
          class="form-check-input"
          type="checkbox"
          @change="onChange"
        >
        <label
          class="form-check-label"
          for="enableWavelogSwitch"
        >{{ $t('settings.enable') }}</label>
      </div>
    </label>
  </div>

  <!-- Wavelog Host -->
  <div class="input-group input-group-sm mb-1">
    <label class="input-group-text w-50 text-wrap">
      {{ $t('settings.chat.adifloggingwaveloghost') }}

      <button
        type="button"
        class="btn btn-link p-0 ms-2"
        data-bs-toggle="tooltip"
        :title="$t('settings.chat.adifloggingwaveloghost_help')"
      >
        <i class="bi bi-question-circle" />
      </button>
    </label>
    <input
      id="wavelogHost"
      v-model="settings.remote.QSO_LOGGING.adif_wavelog_host"
      type="text"
      class="form-control"
      :placeholder="$t('settings.chat.adifloggingwaveloghost_placeholder')"
      @change="onChange"
    >
  </div>
  <div class="input-group input-group-sm mb-1">
    <label class="input-group-text w-50 text-wrap">
      {{ $t('settings.chat.adifloggingwavelogapi') }}
      <button
        type="button"
        class="btn btn-link p-0 ms-2"
        data-bs-toggle="tooltip"
        :title="$t('settings.chat.adifloggingwavelogapi_help')"
      >
        <i class="bi bi-question-circle" />
      </button>
    </label>
    <input
      id="wavelogApiKey"
      v-model="settings.remote.QSO_LOGGING.adif_wavelog_api_key"
      :type="showWavelogApiKey ? 'text' : 'password'"
      class="form-control"
      :placeholder="$t('settings.chat.adifloggingwavelogapi_placeholder')"
      autocomplete="off"
      @change="onChange"
    >
    <button
      type="button"
      class="btn btn-outline-secondary"
      :aria-label="showWavelogApiKey ? 'Hide API key' : 'Show API key'"
      @click="showWavelogApiKey = !showWavelogApiKey"
    >
      <i :class="showWavelogApiKey ? 'bi bi-eye-slash' : 'bi bi-eye'" />
    </button>
  </div>
</template>

