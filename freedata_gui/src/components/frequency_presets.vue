<script setup>
// Frequency presets of the frequency selection: the list to tune from, and
// an edit mode that saves them to the server's config file
// (FREQUENCIES.presets, POST /radio/frequencies). Each preset can carry a
// schedule (UTC) of when it is in use, for example during a contest; it is
// shown as information only and never tunes the radio.
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import { setActivePinia } from "pinia";
import pinia from "../store/index";
setActivePinia(pinia);
import { useStateStore } from "../store/stateStore.js";
import { getRadioFrequencies, setRadioFrequencies, resetRadioFrequencies } from "../js/api";
import { displayToast } from "../js/popupHandler";
import i18next from "../js/i18n";

const state = useStateStore(pinia);
const emit = defineEmits(["select"]);

const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

const slots = ref([]);
const entries = ref([]);
const slotCount = ref(0);
const editing = ref(false);
const draft = ref([]);
const saving = ref(false);
// re-renders the "in 2 h" texts while the list is open
const now = ref(Date.now());
let timer = null;

// a window opened or closed since the list was loaded: the server's state
// (and the next window) is out of date
function stateCrossed(entry) {
  const s = entry.schedule_state;
  if (s?.state === "active") return Date.parse(s.end) <= now.value;
  if (s?.state === "upcoming") return Date.parse(s.start) <= now.value;
  return false;
}

onMounted(() => {
  timer = setInterval(() => {
    now.value = Date.now();
    if (!editing.value && entries.value.some(stateCrossed)) reload();
  }, 30000);
});
onBeforeUnmount(() => clearInterval(timer));

function apply(response) {
  if (!response || !Array.isArray(response.slots)) return false;
  slots.value = response.slots;
  entries.value = Array.isArray(response.frequencies) ? response.frequencies : [];
  slotCount.value = response.slot_count || response.slots.length;
  return true;
}

async function reload() {
  now.value = Date.now();
  if (editing.value) return;
  apply(await getRadioFrequencies());
}
defineExpose({ reload });

// active ones first, the rest in slot order
const sortedEntries = computed(() => {
  const active = entries.value.filter((e) => e.schedule_state?.state === "active");
  const rest = entries.value.filter((e) => e.schedule_state?.state !== "active");
  return [...active, ...rest];
});

// Hz to MHz with at least three decimals: 7053000 -> "7.053", 14093500 -> "14.0935"
function formatMHz(frequency) {
  return (frequency / 1e6).toFixed(6).replace(/0{1,3}$/, "");
}

function parseMHz(text) {
  const value = Number(String(text).trim().replace(",", "."));
  if (!Number.isFinite(value) || value <= 0) return null;
  const hz = Math.round(value * 1e6);
  return hz > 0 ? hz : null;
}

function dayName(day) {
  // 2026-11-16 is a Monday
  const date = new Date(Date.UTC(2026, 10, 16 + DAYS.indexOf(day)));
  return date.toLocaleDateString(i18next.language || undefined, { weekday: "short", timeZone: "UTC" });
}

function utcTime(iso) {
  return new Date(iso).toISOString().slice(11, 16);
}

function localTime(iso) {
  return new Date(iso).toLocaleString(i18next.language || undefined, {
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function duration(ms) {
  const minutes = Math.max(0, Math.round(ms / 60000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} h ${minutes % 60} min`;
  return `${Math.round(hours / 24)} d`;
}

function scheduleText(schedule) {
  const days = schedule.days.length ? schedule.days.map(dayName).join(", ") : i18next.t("grid.frequencypresets.everyday");
  // equal times are 24 h; an earlier end is on the next day
  let span = "";
  if (schedule.end === schedule.start) span = " (24 h)";
  else if (schedule.end < schedule.start) span = " (+1)";
  let text = `${days} ${schedule.start}–${schedule.end} UTC${span}`;
  if (schedule.from || schedule.to) text += ` · ${schedule.from || "…"} – ${schedule.to || "…"}`;
  return text;
}

// the state badge of a preset with a schedule: {cls, text, title}
function stateBadge(entry) {
  const s = entry.schedule_state;
  if (!s) return null;
  const t = (key) => i18next.t(`grid.frequencypresets.${key}`);
  switch (s.state) {
    case "active": {
      const left = Date.parse(s.end) - now.value;
      if (left <= 0) return { cls: "text-bg-secondary", text: t("over"), title: "" };
      return {
        cls: "text-bg-success",
        text: `${t("now")} · ${t("until")} ${utcTime(s.end)} UTC`,
        title: `${localTime(s.start)} – ${localTime(s.end)}`,
      };
    }
    case "upcoming": {
      const until = Date.parse(s.start) - now.value;
      if (until <= 0) return { cls: "text-bg-success", text: t("now"), title: "" };
      return {
        cls: "text-bg-info",
        text: `${t("in")} ${duration(until)}`,
        title: `${localTime(s.start)} – ${localTime(s.end)}`,
      };
    }
    case "ended":
      return { cls: "text-bg-secondary", text: t("ended"), title: "" };
    default:
      return { cls: "text-bg-light border", text: t("scheduled"), title: "" };
  }
}

// ---- edit mode ----

function toDraft(slot) {
  if (!slot) return null;
  return {
    mhz: formatMHz(slot.frequency),
    band: slot.band || "",
    notes: slot.notes || "",
    hasSchedule: !!slot.schedule,
    schedule: slot.schedule
      ? { ...slot.schedule, days: [...slot.schedule.days] }
      : { days: [], start: "00:00", end: "00:00", from: "", to: "" },
  };
}

function startEditing() {
  const list = slots.value.map(toDraft);
  while (list.length < slotCount.value) list.push(null);
  draft.value = list;
  editing.value = true;
}

function cancelEditing() {
  editing.value = false;
  reload();
}

function radioMHz() {
  const hz = Number(state.frequency);
  return hz > 0 ? formatMHz(hz) : "";
}

function fillSlot(index) {
  draft.value[index] = toDraft({ frequency: 1, band: "", notes: "", schedule: null });
  draft.value[index].mhz = radioMHz();
}

function useRadioFrequency(index) {
  const mhz = radioMHz();
  if (mhz) {
    draft.value[index].mhz = mhz;
    // the band label is filled in again by the server
    draft.value[index].band = "";
  }
}

function clearSlot(index) {
  draft.value[index] = null;
}

function move(index, step) {
  const target = index + step;
  if (target < 0 || target >= draft.value.length) return;
  const list = [...draft.value];
  [list[index], list[target]] = [list[target], list[index]];
  draft.value = list;
}

function toggleDay(slot, day) {
  const days = slot.schedule.days;
  const at = days.indexOf(day);
  if (at >= 0) days.splice(at, 1);
  else days.push(day);
}

function slotError(slot) {
  if (!slot) return "";
  if (parseMHz(slot.mhz) === null) return i18next.t("grid.frequencypresets.invalidfrequency");
  if (slot.hasSchedule) {
    const s = slot.schedule;
    if (!/^\d\d:\d\d$/.test(s.start) || !/^\d\d:\d\d$/.test(s.end)) {
      return i18next.t("grid.frequencypresets.invalidtime");
    }
    if (s.from && s.to && s.from > s.to) return i18next.t("grid.frequencypresets.invaliddates");
  }
  return "";
}

const draftValid = computed(() => draft.value.every((slot) => !slotError(slot)));

function fromDraft(slot) {
  if (!slot) return null;
  return {
    frequency: parseMHz(slot.mhz),
    band: slot.band.trim(),
    notes: slot.notes.trim(),
    schedule: slot.hasSchedule
      ? {
          days: DAYS.filter((d) => slot.schedule.days.includes(d)),
          start: slot.schedule.start,
          end: slot.schedule.end,
          from: slot.schedule.from || "",
          to: slot.schedule.to || "",
        }
      : null,
  };
}

async function save() {
  if (!draftValid.value) return;
  saving.value = true;
  try {
    // errors are shown by api.js; stay in edit mode then
    if (apply(await setRadioFrequencies(draft.value.map(fromDraft)))) {
      editing.value = false;
      displayToast("success", "bi-check-circle", i18next.t("grid.frequencypresets.saved"), 3000);
    }
  } finally {
    saving.value = false;
  }
}

async function resetToDefaults() {
  if (!window.confirm(i18next.t("grid.frequencypresets.reset_confirm"))) return;
  saving.value = true;
  try {
    if (apply(await resetRadioFrequencies())) {
      editing.value = false;
    }
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <!-- list to tune from -->
  <template v-if="!editing">
    <div class="d-flex justify-content-end mb-2">
      <button
        type="button"
        class="btn btn-sm btn-outline-secondary"
        :title="$t('grid.frequencypresets.edit_help')"
        @click="startEditing"
      >
        <i class="bi bi-pencil me-1" />{{ $t('grid.frequencypresets.edit') }}
      </button>
    </div>
    <ul class="list-group">
      <li
        v-if="sortedEntries.length === 0"
        class="list-group-item text-muted"
      >
        {{ $t('grid.frequencypresets.none') }}
      </li>
      <a
        v-for="entry in sortedEntries"
        :key="entry.slot"
        href="#"
        class="list-group-item list-group-item-action"
        data-bs-dismiss="offcanvas"
        @click.prevent="emit('select', entry.frequency)"
      >
        <div class="d-flex w-100 justify-content-between">
          <h5 class="mb-1">{{ formatMHz(entry.frequency) }} MHz</h5>
          <small class="text-break text-center mx-2">{{ entry.notes }}</small>
          <h6>{{ entry.band }}</h6>
        </div>
        <div
          v-if="entry.schedule"
          class="d-flex flex-wrap align-items-center gap-2 small"
        >
          <span
            class="badge"
            :class="stateBadge(entry).cls"
            :title="stateBadge(entry).title"
          >{{ stateBadge(entry).text }}</span>
          <span class="text-muted">{{ scheduleText(entry.schedule) }}</span>
        </div>
      </a>
    </ul>
  </template>

  <!-- edit mode -->
  <template v-else>
    <p class="small text-muted">
      {{ $t('grid.frequencypresets.edit_description', { count: slotCount }) }}
    </p>
    <div
      v-for="(slot, index) in draft"
      :key="index"
      class="card mb-2"
    >
      <div class="card-body p-2">
        <div class="d-flex align-items-center mb-1">
          <span class="badge text-bg-secondary me-2">{{ index + 1 }}</span>
          <span
            v-if="!slot"
            class="text-muted flex-grow-1"
          >{{ $t('grid.frequencypresets.empty') }}</span>
          <span
            v-else
            class="flex-grow-1"
          />
          <div class="btn-group btn-group-sm">
            <button
              type="button"
              class="btn btn-outline-secondary"
              :disabled="index === 0"
              :title="$t('grid.frequencypresets.moveup')"
              @click="move(index, -1)"
            >
              <i class="bi bi-arrow-up" />
            </button>
            <button
              type="button"
              class="btn btn-outline-secondary"
              :disabled="index === draft.length - 1"
              :title="$t('grid.frequencypresets.movedown')"
              @click="move(index, 1)"
            >
              <i class="bi bi-arrow-down" />
            </button>
            <button
              v-if="!slot"
              type="button"
              class="btn btn-outline-success"
              :title="$t('grid.frequencypresets.add_help')"
              @click="fillSlot(index)"
            >
              <i class="bi bi-plus-lg" />
            </button>
            <button
              v-else
              type="button"
              class="btn btn-outline-danger"
              :title="$t('grid.frequencypresets.clear_help')"
              @click="clearSlot(index)"
            >
              <i class="bi bi-trash" />
            </button>
          </div>
        </div>

        <template v-if="slot">
          <div class="input-group input-group-sm mb-1">
            <input
              v-model="slot.mhz"
              type="text"
              inputmode="decimal"
              class="form-control"
              :class="{ 'is-invalid': parseMHz(slot.mhz) === null }"
              :aria-label="$t('grid.frequencypresets.frequency')"
              :placeholder="$t('grid.frequencypresets.frequency')"
            >
            <span class="input-group-text">MHz</span>
            <button
              type="button"
              class="btn btn-outline-secondary"
              :disabled="!radioMHz()"
              :title="$t('grid.frequencypresets.useradio_help')"
              @click="useRadioFrequency(index)"
            >
              <i class="bi bi-broadcast" />
            </button>
          </div>
          <div class="input-group input-group-sm mb-1">
            <input
              v-model="slot.band"
              type="text"
              maxlength="20"
              class="form-control"
              style="max-width: 6rem"
              :aria-label="$t('grid.frequencypresets.band')"
              :placeholder="$t('grid.frequencypresets.band')"
              :title="$t('grid.frequencypresets.band_help')"
            >
            <input
              v-model="slot.notes"
              type="text"
              maxlength="200"
              class="form-control"
              :aria-label="$t('grid.frequencypresets.notes')"
              :placeholder="$t('grid.frequencypresets.notes')"
            >
          </div>
          <div class="form-check form-switch small">
            <input
              :id="`preset-schedule-${index}`"
              v-model="slot.hasSchedule"
              class="form-check-input"
              type="checkbox"
            >
            <label
              class="form-check-label"
              :for="`preset-schedule-${index}`"
            >{{ $t('grid.frequencypresets.schedule') }}</label>
          </div>
          <div
            v-if="slot.hasSchedule"
            class="border rounded p-2 mt-1 small"
          >
            <div
              class="btn-group btn-group-sm flex-wrap mb-1"
              role="group"
              :title="$t('grid.frequencypresets.days_help')"
            >
              <button
                v-for="day in DAYS"
                :key="day"
                type="button"
                class="btn"
                :class="slot.schedule.days.includes(day) ? 'btn-primary' : 'btn-outline-primary'"
                @click="toggleDay(slot, day)"
              >
                {{ dayName(day) }}
              </button>
            </div>
            <div class="input-group input-group-sm mb-1">
              <span class="input-group-text">UTC</span>
              <input
                v-model="slot.schedule.start"
                type="time"
                class="form-control"
                :aria-label="$t('grid.frequencypresets.start')"
                :title="$t('grid.frequencypresets.start')"
              >
              <span class="input-group-text">–</span>
              <input
                v-model="slot.schedule.end"
                type="time"
                class="form-control"
                :aria-label="$t('grid.frequencypresets.end')"
                :title="$t('grid.frequencypresets.end')"
              >
            </div>
            <div class="input-group input-group-sm mb-1">
              <input
                v-model="slot.schedule.from"
                type="date"
                class="form-control"
                :aria-label="$t('grid.frequencypresets.from')"
                :title="$t('grid.frequencypresets.from')"
              >
              <span class="input-group-text">–</span>
              <input
                v-model="slot.schedule.to"
                type="date"
                class="form-control"
                :aria-label="$t('grid.frequencypresets.to')"
                :title="$t('grid.frequencypresets.to')"
              >
            </div>
            <div class="text-muted">
              {{ $t('grid.frequencypresets.schedule_help') }}
            </div>
          </div>
          <div
            v-if="slotError(slot)"
            class="text-danger small mt-1"
          >
            {{ slotError(slot) }}
          </div>
        </template>
      </div>
    </div>

    <div class="d-flex flex-wrap gap-2 mt-2">
      <button
        type="button"
        class="btn btn-sm btn-success"
        :disabled="!draftValid || saving"
        @click="save"
      >
        <i class="bi bi-check-lg me-1" />{{ $t('grid.frequencypresets.save') }}
      </button>
      <button
        type="button"
        class="btn btn-sm btn-outline-secondary"
        :disabled="saving"
        @click="cancelEditing"
      >
        {{ $t('grid.frequencypresets.cancel') }}
      </button>
      <button
        type="button"
        class="btn btn-sm btn-outline-danger ms-auto"
        :disabled="saving"
        :title="$t('grid.frequencypresets.reset_help')"
        @click="resetToDefaults"
      >
        <i class="bi bi-arrow-counterclockwise me-1" />{{ $t('grid.frequencypresets.reset') }}
      </button>
    </div>
  </template>
</template>
