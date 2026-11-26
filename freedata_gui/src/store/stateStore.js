import { defineStore } from "pinia";
import { ref } from "vue";

export const useStateStore = defineStore("stateStore", () => {
  var busy_state = ref();
  var frequency = ref(0);
  var new_frequency = ref(14093000);
  // radio frequency is inside an amateur band (IARU band plan)
  var amateur_band = ref(false);
  // an encrypted message was not sent, because the radio is on an amateur band;
  // cleared once the radio leaves the amateur bands
  var encryption_band_blocked = ref(false);
  // the server's temp-directory log file, "" when it is not logging there
  var temp_log_file = ref("");
  var mode = ref("-");
  var rf_level = ref(10);

  var swr_raw = ref(0);
  var swr_percent = ref(0);

  var tuner = ref();

  var dbfs_level_percent = ref(0);
  var dbfs_level = ref(0);
  var radio_status = ref(false);

  var ptt_state = ref(false);

  var speed_level = ref(0);
  var channel_busy = ref(false);
  var channel_busy_slot = ref([false, false, false, false, false]);
  var scatter = ref([]);
  var s_meter_strength_percent = ref(0);
  var s_meter_strength_raw = ref(0);

  var modem_connection = ref("disconnected");
  var is_modem_running = ref();

  var arq_total_bytes = ref(0);
  var arq_transmission_percent = ref(0);

  var activities = ref([]);
  var heard_stations = ref([]);
  var dxcallsign = ref("");

  var beacon_state = ref(false);
  var away_from_key = ref(false);

  // RX audio blocks lost since the modem started: driver, queue, decoder
  var rx_audio_loss = ref({ driver: 0, queue: 0, decoder: 0 });

  var is_codec2_traffic = ref("");

  var arq_speed_list_timestamp = ref([]);
  var arq_speed_list_bpm = ref([]);
  var arq_speed_list_snr = ref([]);
  var arq_bytes_per_minute = ref();
  var arq_bits_per_second = ref();

  var arq_is_receiving = ref(false);

  // Role of this station in the running ARQ session: "ISS" (sending),
  // "IRS" (receiving) or "" when no session is running
  var arq_role = ref("");
  // Call sign of the remote station in the running ARQ session
  var arq_remote_call = ref("");
  // Data mode of the running ARQ session's speed level (FREEDV_MODE name,
  // such as "datac4"), "" until the first progress event names it
  var arq_mode = ref("");

  /* TODO Those 3 can be removed I guess , DJ2LS*/

  var api_version = ref();
  var modem_version = ref();

  var os_info = ref({
    system: "",
    node: "",
    release: "",
    version: "",
    machine: "",
    processor: "",
  });
  var python_info = ref({
    build: ["", ""],
    compiler: "",
    branch: "",
    implementation: "",
    revision: "",
    version: "",
  });

  // New state for network traffic
  var is_network_traffic = ref(false);

  return {
    dxcallsign,
    busy_state,
    new_frequency,
    frequency,
    amateur_band,
    encryption_band_blocked,
    temp_log_file,
    mode,
    tuner,
    swr_raw,
    swr_percent,
    dbfs_level,
    dbfs_level_percent,
    speed_level,
    channel_busy,
    channel_busy_slot,
    scatter,
    ptt_state,
    s_meter_strength_percent,
    s_meter_strength_raw,
    arq_total_bytes,
    rx_audio_loss,
    arq_transmission_percent,
    arq_speed_list_bpm,
    arq_speed_list_timestamp,
    arq_speed_list_snr,
    arq_bytes_per_minute,
    arq_bits_per_second,
    arq_is_receiving,
    arq_role,
    arq_remote_call,
    arq_mode,
    modem_connection,
    is_modem_running,
    is_codec2_traffic,
    rf_level,
    activities,
    heard_stations,
    beacon_state,
    away_from_key,
    python_info,
    modem_version,
    api_version,
    os_info,
    radio_status,
    is_network_traffic,
  };
});
