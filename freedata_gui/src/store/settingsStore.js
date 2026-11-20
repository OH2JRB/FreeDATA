import { reactive, watch } from "vue";
import { getConfig, setConfig } from "../js/api";
import { displayToast } from "../js/popupHandler";

// Default configuration
const defaultConfig = {
  local: {
    host: "127.0.0.1",
    port: "5000",
    spectrum: "waterfall",
    wf_theme: 2,
    enable_sys_notification: false,
    grid_layout: "[]",
    grid_preset: "[]",
    grid_enabled: true,
    language: "en",
    colormode: "light",
  },
  remote: {
    AUDIO: {
      input_device: "",
      output_device: "",
      rx_audio_level: 0,
      tx_audio_level: 0,
      rx_auto_audio_level: true,
      tx_auto_audio_level: false,
      rx_buffer_ms: 0,
    },
    MODEM: {
      tx_delay: 0,
      enable_morse_identifier: false,
      maximum_bandwidth: 2438,
      enable_fsk: false,
      enable_high_rate: true,
    },
    NETWORK: {
      modemaddress: "127.0.0.1",
      modemport: 5000,
      allowed_hosts: [],
    },
    RADIO: {
      control: "disabled",
      model_id: 0,
      serial_port: "",
      serial_speed: 0,
      data_bits: 0,
      stop_bits: 0,
      serial_handshake: "",
      ptt_port: "",
      ptt_type: "",
      ptt_mode: "",
      serial_dcd: "",
      serial_dtr: "",
      serial_rts: "",
    },
    RIGCTLD: {
      ip: "127.0.0.1",
      port: 0,
      path: "",
      command: "",
      arguments: "",
      enable_vfo: false,
    },
    FLRIG: {
      ip: "127.0.0.1",
      port: 12345,
    },
    STATION: {
      enable_explorer: false,
      enable_stats: false,
      mycall: "DEFAULT",
      myssid: 0,
      mygrid: "",
      ssid_list: [],
      respond_to_cq: false,
      iaru_region: 0,
      enable_callsign_blacklist: false,
      callsign_blacklist: [],
    },
    MESSAGES: {
      enable_auto_repeat: false,
      enable_common_key: false,
    },
    SOCKET_INTERFACE: {
      enable: false,
      host: "127.0.0.1",
      cmd_port: 8000,
      data_port: 8001,
    },

    QSO_LOGGING: {
      enable_adif_udp: false,
      adif_udp_host: "127.0.0.1",
      adif_udp_port: 2237,
      enable_adif_wavelog: false,
      adif_wavelog_host: "127.0.0.1",
      adif_wavelog_api_key: "",
    },

    GUI: {
      auto_run_browser: true,
      distance_unit: "km",
    },
    EXP: {
      enable_ring_buffer: false,
      enable_vhf: false,
      enable_groupchat: false,
    },
  },
};

// Deep copy of plain JSON data
function clone(obj) {
  return JSON.parse(JSON.stringify(obj));
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

// Recursively merge `override` over `base`, returns a new object.
// Keys missing in `override` keep their default value.
function deepMerge(base, override) {
  const result = clone(base);
  if (!isPlainObject(override)) return result;
  for (const [key, value] of Object.entries(override)) {
    if (value === undefined) continue;
    result[key] =
      isPlainObject(value) && isPlainObject(result[key])
        ? deepMerge(result[key], value)
        : value;
  }
  return result;
}

// Initialize local settings from browser storage, merged over the defaults
// so keys added in newer versions get their default value
function loadLocalConfig() {
  try {
    const stored = JSON.parse(localStorage.getItem("localConfig"));
    return deepMerge(defaultConfig.local, stored);
  } catch (error) {
    console.error("Failed to load local config, using defaults:", error);
    return clone(defaultConfig.local);
  }
}
const localConfig = loadLocalConfig();
console.log("--------- LOCAL CONFIG -----------");
console.log(localConfig);

export const settingsStore = reactive({
  local: localConfig,
  remote: clone(defaultConfig.remote),
});

// true once a config has been received from the server
let remoteConfigLoaded = false;

// Apply a config received from the server to the store. The callsign
// blacklist is edited as newline separated text in the GUI.
// The config as the server last sent it. A save sends only the settings that
// differ from it (changedSettings): a tab that sent its whole copy wrote back
// everything it had read earlier, including what another tab had changed
// meanwhile (on 2026-11-20 an older tab put the old call sign back when the
// SSID was changed in it).
let serverConfig = {};

// The callsign blacklist is edited as text, one call per line, but stored
// and sent as a list
function blacklistAsList(value) {
  if (typeof value === "string") {
    return value
      .split("\n")
      .map((item) => item.trim())
      .filter((item) => item !== "");
  }
  return Array.isArray(value) ? value : [];
}

// A setting as this tab would send it / as the server sent it
function sendable(section, key, value) {
  return section === "STATION" && key === "callsign_blacklist"
    ? blacklistAsList(value)
    : value;
}
function shown(section, key, value) {
  return section === "STATION" &&
    key === "callsign_blacklist" &&
    Array.isArray(value)
    ? value.join("\n")
    : value;
}

// Another tab or client changed the config (the server's "config changed"
// event): take over its values, but only for the settings this tab has not
// changed itself since it last received the config, so an edit in progress
// here is kept and saved later as before. Nothing is saved here.
export function mergeRemoteConfig(conf) {
  for (const [section, settings] of Object.entries(conf || {})) {
    if (!settings || typeof settings !== "object") continue;
    if (!settingsStore.remote[section]) {
      settingsStore.remote[section] = {};
    }
    for (const [key, value] of Object.entries(settings)) {
      const local = sendable(section, key, settingsStore.remote[section][key]);
      const base = serverConfig?.[section]?.[key];
      if (JSON.stringify(local) === JSON.stringify(base)) {
        settingsStore.remote[section][key] = shown(section, key, value);
      }
    }
  }
  serverConfig = clone(conf);
}

export function refreshRemote() {
  if (!remoteConfigLoaded) return getRemote();
  return getConfig().then((conf) => {
    if (conf && typeof conf === "object") mergeRemoteConfig(conf);
  });
}

function applyRemoteConfig(conf) {
  remoteConfigLoaded = true;
  serverConfig = clone(conf);
  settingsStore.remote = conf;
  const blacklist = conf?.STATION?.callsign_blacklist;
  if (Array.isArray(blacklist)) {
    settingsStore.remote.STATION.callsign_blacklist = blacklist.join("\n");
  }
}

// Function to handle remote configuration changes
// The settings in `current` that differ from `base`, by section
export function changedSettings(current, base) {
  const changes = {};
  for (const [section, settings] of Object.entries(current || {})) {
    if (!settings || typeof settings !== "object") continue;
    for (const [key, value] of Object.entries(settings)) {
      if (JSON.stringify(value) !== JSON.stringify(base?.[section]?.[key])) {
        (changes[section] ??= {})[key] = value;
      }
    }
  }
  return changes;
}

export function onChange() {
  // Never post the GUI defaults over the server config
  if (!remoteConfigLoaded) {
    console.warn("Configuration not loaded from server yet, not saving");
    displayToast(
      "warning",
      "bi-exclamation-triangle",
      "Settings not saved: configuration not loaded from server yet",
      5000,
    );
    return getRemote();
  }

  // Work on a copy so a failed request does not leave the store modified
  let remote_config = clone(settingsStore.remote);
  let blacklistContent = remote_config.STATION?.callsign_blacklist;
  // Check if the content is a string
  if (typeof blacklistContent === "string") {
    // Split the string by newlines to create an array
    blacklistContent = blacklistContent
      .split("\n") // Split text by newlines
      .map((item) => item.trim()) // Trim whitespace from each line
      .filter((item) => item !== ""); // Remove empty lines
  }

  // Ensure it's an array, even if the data comes in incorrectly formatted
  if (!Array.isArray(blacklistContent)) {
    // Convert any other data types to an empty array as a fallback
    blacklistContent = [];
  }
  if (remote_config.STATION) {
    remote_config.STATION.callsign_blacklist = blacklistContent;
  }
  // The frequency presets are saved on their own (POST /radio/frequencies);
  // the copy here may be older than what is stored
  delete remote_config.FREQUENCIES;

  // only what this tab changed; the server merges it into the stored config
  // and answers with the whole of it, other tabs' changes included
  const changes = changedSettings(remote_config, serverConfig);
  if (Object.keys(changes).length === 0) return Promise.resolve();

  return setConfig(changes).then((conf) => {
    if (conf && typeof conf === "object") {
      applyRemoteConfig(conf);
    } else {
      // Saving failed (the error is shown by api.js). Reload the config from
      // the server so the UI shows what is actually stored.
      console.warn("Saving configuration failed, reloading from server");
      return getRemote();
    }
  });
}

// Function to fetch remote configuration. Only loads, never posts.
export function getRemote() {
  return getConfig().then((conf) => {
    if (conf && typeof conf === "object") {
      applyRemoteConfig(conf);
    } else {
      // keep the current values (defaults until the first successful load)
      console.warn("Received undefined configuration, keeping current values");
    }
  });
}

// Watcher to save local settings on change
watch(
  () => settingsStore.local,
  () => {
    saveLocalSettingsToConfig();
  },
  { deep: true },
);

// Function to save local settings to browser storage
export function saveLocalSettingsToConfig() {
  try {
    localStorage.setItem("localConfig", JSON.stringify(settingsStore.local));
    console.log("Settings saved!");
  } catch (error) {
    console.error("Failed to save local settings:", error);
  }
}
