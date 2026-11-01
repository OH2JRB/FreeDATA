<script setup>
// The message queue: messages waiting to be sent, in the order they go out.
// A queued message can be deleted or moved behind all others ("send last");
// the message being sent is shown first, without actions.
import { onMounted } from "vue";
import i18next from "../js/i18n";

import {
  getFreedataQueue,
  deleteFreedataQueuedMessage,
  sendFreedataQueuedMessageLast,
} from "../js/api";
import { parseServerTimestamp } from "../js/messagesHandler";

import { setActivePinia } from "pinia";
import pinia from "../store/index";
setActivePinia(pinia);

import { useChatStore } from "../store/chatStore.js";
const chat = useChatStore(pinia);

onMounted(getFreedataQueue);

function created(message) {
  if (!message.timestamp) return "";
  // like the chat bubbles: UTC
  return parseServerTimestamp(message.timestamp)
    .toISOString()
    .replace("T", " ")
    .slice(0, 16);
}

function preview(message) {
  const text = (message.body || "").replace(/<[^>]*>/g, " ").trim();
  return text.length > 80 ? `${text.slice(0, 80)}…` : text;
}

async function sendLast(message) {
  await sendFreedataQueuedMessageLast(message.id);
  getFreedataQueue();
}

async function remove(message) {
  if (!confirm(i18next.t("queue.deleteconfirm", { destination: message.destination }))) return;
  await deleteFreedataQueuedMessage(message.id);
  getFreedataQueue();
}
</script>

<template>
  <div
    class="container-fluid p-3 overflow-auto"
    style="height: calc(100vh - 48px);"
  >
    <div class="d-flex align-items-center mb-1">
      <h5 class="mb-0 me-2">
        {{ $t('queue.title') }}
      </h5>
      <span class="badge text-bg-secondary me-auto">{{ chat.queue.length }}</span>
      <button
        type="button"
        class="btn btn-sm btn-outline-secondary"
        @click="getFreedataQueue()"
      >
        <i class="bi bi-arrow-clockwise me-1" />{{ $t('queue.refresh') }}
      </button>
    </div>
    <p class="text-muted small">
      {{ $t('queue.description') }}
    </p>

    <div
      v-if="chat.queue.length === 0"
      class="text-muted"
    >
      {{ $t('queue.empty') }}
    </div>

    <div
      v-else
      class="table-responsive"
    >
      <table class="table table-sm table-hover align-middle">
        <thead>
          <tr>
            <th>{{ $t('queue.position') }}</th>
            <th>{{ $t('queue.to') }}</th>
            <th>{{ $t('queue.message') }}</th>
            <th>{{ $t('queue.queued') }}</th>
            <th>{{ $t('queue.attempt') }}</th>
            <th>{{ $t('queue.status') }}</th>
            <th class="text-end">
              {{ $t('queue.actions') }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="message in chat.queue"
            :key="message.id"
          >
            <td>{{ message.position }}</td>
            <td class="text-nowrap">
              {{ message.destination }}
              <i
                v-if="message.encrypted"
                class="bi bi-lock-fill text-success"
              />
            </td>
            <td>
              <div>{{ preview(message) }}</div>
              <div
                v-for="attachment in message.attachments"
                :key="attachment.name"
                class="small text-muted"
              >
                <i class="bi bi-paperclip" />{{ attachment.name }}
              </div>
            </td>
            <td class="text-nowrap small">
              {{ created(message) }} UTC
            </td>
            <td>{{ message.attempt + 1 }}</td>
            <td>
              <span
                class="badge"
                :class="message.status === 'transmitting' ? 'text-bg-primary' : 'text-bg-secondary'"
              >
                {{ message.status === 'transmitting' ? $t('queue.transmitting') : $t('queue.queuedstatus') }}
              </span>
            </td>
            <td class="text-end text-nowrap">
              <template v-if="message.status === 'queued'">
                <button
                  type="button"
                  class="btn btn-sm btn-outline-secondary me-1"
                  :title="$t('queue.sendlast_help')"
                  :disabled="message.position === chat.queue.length"
                  @click="sendLast(message)"
                >
                  <i class="bi bi-arrow-down-square me-1" />{{ $t('queue.sendlast') }}
                </button>
                <button
                  type="button"
                  class="btn btn-sm btn-outline-danger"
                  :title="$t('queue.delete_help')"
                  @click="remove(message)"
                >
                  <i class="bi bi-trash me-1" />{{ $t('queue.delete') }}
                </button>
              </template>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
