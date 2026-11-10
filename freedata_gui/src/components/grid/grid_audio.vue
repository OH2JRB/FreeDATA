<script setup>
// Listen to the received radio audio in the browser. The /audio_rx socket is
// opened only while listening; see js/audioStreamHandler.js for the player.
import { setActivePinia } from 'pinia';
import { ref, computed, watch, onBeforeUnmount } from 'vue';

import pinia from '../../store/index';
setActivePinia(pinia);

import { useStateStore } from '@/store/stateStore';
const state = useStateStore(pinia);

import { openAudioStream } from '../../js/event_sock.js';
import { RxAudioPlayer, MAX_VOLUME } from '../../js/audioStreamHandler.js';

// no audio for this long while listening -> "waiting for audio"
const NO_AUDIO_MS = 1500;
const VOLUME_KEY = 'rxAudioVolume';
const MUTED_KEY = 'rxAudioMuted';

function readSetting(key, fallback) {
  try {
    const value = localStorage.getItem(key);
    return value === null ? fallback : JSON.parse(value);
  } catch {
    return fallback;
  }
}

function saveSetting(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // private window or storage blocked: the setting lasts for this page only
  }
}

const player = new RxAudioPlayer();
let stream = null;
let statusTimer = null;

const isPlaying = ref(false);
const hasAudio = ref(false);
const volumePercent = ref(Number(readSetting(VOLUME_KEY, 100)) || 0);
const muted = ref(Boolean(readSetting(MUTED_KEY, false)));
const maxPercent = MAX_VOLUME * 100;

player.setVolume(volumePercent.value / 100);
player.setMuted(muted.value);

const status = computed(() => {
  if (!isPlaying.value) return '';
  if (!state.is_modem_running) return 'grid.components.audiostream_modem_stopped';
  if (state.ptt_state) return 'grid.components.audiostream_tx';
  if (!hasAudio.value) return 'grid.components.audiostream_waiting';
  if (muted.value) return 'grid.components.audiostream_muted';
  return 'grid.components.audiostream_listening';
});

async function startListening() {
  if (isPlaying.value) return;
  try {
    // inside the click handler, so the browser lets the audio play
    await player.start();
  } catch (err) {
    console.error('Could not start audio output', err);
    player.stop();
    return;
  }
  player.setTransmitting(state.ptt_state);
  stream = openAudioStream((data) => player.push(data));
  isPlaying.value = true;
  statusTimer = setInterval(() => {
    hasAudio.value = performance.now() - player.lastBlockAt < NO_AUDIO_MS;
  }, 500);
}

function stopListening() {
  if (stream) {
    stream.close();
    stream = null;
  }
  if (statusTimer !== null) {
    clearInterval(statusTimer);
    statusTimer = null;
  }
  player.stop();
  isPlaying.value = false;
  hasAudio.value = false;
}

function toggleListening() {
  if (isPlaying.value) {
    stopListening();
  } else {
    startListening();
  }
}

function toggleMute() {
  muted.value = !muted.value;
}

watch(volumePercent, (value) => {
  player.setVolume(Number(value) / 100);
  saveSetting(VOLUME_KEY, Number(value));
});

watch(muted, (value) => {
  player.setMuted(value);
  saveSetting(MUTED_KEY, value);
});

watch(
  () => state.ptt_state,
  (ptt) => player.setTransmitting(ptt),
);

onBeforeUnmount(() => {
  stopListening();
});
</script>

<template>
  <div class="card h-100">
    <div class="card-header p-1 ps-2">
      <i class="bi bi-headphones me-1"></i>
      <strong>{{ $t('grid.components.audiostream') }}</strong>
    </div>
    <div class="card-body p-2 overflow-auto">
      <div class="d-flex align-items-center gap-2">
        <button
          type="button"
          :class="isPlaying ? 'btn btn-sm btn-danger text-nowrap' : 'btn btn-sm btn-success text-nowrap'"
          :title="$t('grid.components.audiostream_help')"
          @click="toggleListening"
        >
          <i :class="isPlaying ? 'bi bi-stop-fill' : 'bi bi-play-fill'"></i>
          {{ isPlaying ? $t('grid.components.audiostream_stop') : $t('grid.components.audiostream_start') }}
        </button>
        <button
          type="button"
          :class="muted ? 'btn btn-sm btn-warning' : 'btn btn-sm btn-outline-secondary'"
          :title="$t('grid.components.audiostream_mute')"
          :aria-pressed="muted"
          @click="toggleMute"
        >
          <i :class="muted ? 'bi bi-volume-mute-fill' : 'bi bi-volume-up-fill'"></i>
        </button>
        <small class="ms-auto text-nowrap">{{ volumePercent }} %</small>
      </div>
      <div class="d-flex align-items-center mt-1">
        <input
          v-model.number="volumePercent"
          type="range"
          class="form-range flex-grow-1"
          min="0"
          :max="maxPercent"
          step="5"
          :title="$t('grid.components.audiostream_volume')"
          :aria-label="$t('grid.components.audiostream_volume')"
        />
      </div>
      <small v-if="status" class="d-block mt-1 text-body-secondary text-truncate" :title="$t(status)">
        <span v-if="status === 'grid.components.audiostream_tx'" class="badge bg-danger me-1">TX</span>
        {{ $t(status) }}
      </small>
    </div>
  </div>
</template>
