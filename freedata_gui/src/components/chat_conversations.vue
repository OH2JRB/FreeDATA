<template>
  <!-- Navbar for starting a new chat -->
  <nav class="navbar sticky-top bg-body-tertiary border-bottom p-1 flex-nowrap gap-1">
    <button
      class="btn btn-outline-primary flex-grow-1"
      data-bs-target="#newChatModal"
      data-bs-toggle="modal"
      @click="startNewChat"
    >
      <i class="bi bi-pencil-square" /> {{ $t('chat.startnewchat') }}
    </button>
    <button
      class="btn btn-outline-secondary"
      :title="$t('chat.messagelog_help')"
      :aria-label="$t('chat.messagelog_help')"
      :disabled="savingMessageLog"
      @click="saveMessageLog"
    >
      <span
        v-if="savingMessageLog"
        class="spinner-border spinner-border-sm"
        aria-hidden="true"
      />
      <i
        v-else
        class="bi bi-filetype-csv"
      />
      <!-- icon only where the sidebar is narrow -->
      <span class="d-none d-xl-inline"> {{ $t('chat.messagelog') }}</span>
    </button>
  </nav>

  <!-- The message log saved on this computer -->
  <div
    v-if="messageLog"
    class="alert alert-success alert-dismissible small m-1 mb-0 p-2 pe-5"
    role="status"
  >
    <strong>{{ $t('chat.messagelog_saved') }}</strong>
    ({{ messageLog.totalMessages }})
    <div class="text-break">
      {{ $t('chat.messagelog_file') }}: <code class="user-select-all">{{ messageLog.fileName }}</code>
    </div>
    <div
      v-if="!messageLog.picked"
      class="text-muted"
    >
      {{ $t('chat.messagelog_downloaded') }}
    </div>
    <button
      type="button"
      class="btn-close p-2"
      aria-label="Close"
      @click="messageLog = null"
    />
  </div>

  <!-- List of chats -->
  <div
    id="chat-list-tab"
    class="list-group m-0 p-1"
    role="tablist"
  >
    <!-- Show loading message if we're waiting -->
    <div
      v-if="chat.loading"
      class="text-center p-2"
    >
      <div
        class="spinner-border"
        role="status"
      >
        <span class="visually-hidden">{{ $t('chat.loadingMessages') }}</span>
      </div>
    </div>

    <!-- Show 'no conversations' message if not loading and no conversations exist -->
    <div
      v-else-if="!chat.callsign_list || Object.keys(chat.callsign_list).length === 0"
      class="text-center p-2"
    >
      {{ $t('chat.noConversations') }}
    </div>


    <template
      v-for="(details, callsign) in chat.callsign_list"
      :key="callsign"
    >
      <a
        :id="`list-chat-list-${callsign}`"
        class="list-group-item list-group-item-action list-group-item-secondary rounded-2 border-0 mb-2"
        data-bs-toggle="list"
        :href="`#list-${callsign}-messages`"
        role="tab"
        :aria-controls="`list-${callsign}-messages`"
        @click="chatSelected(callsign)"
      >
        <div class="row">
          <div class="col-7 text-truncate">
            <strong>{{ callsign }}</strong>
            <span
              v-if="details.unread_messages > 0"
              class="ms-1 badge bg-danger"
            >
              {{ details.unread_messages }} {{ $t('chat.new') }}
            </span>
            <br>
            <small>{{ previewBody(details.body) }}</small>

          </div>
          <div class="col-5 text-end">
            <small>{{ getDateTime(details.timestamp) }}</small>

          </div>
        </div>
      </a>
    </template>
  </div>
</template>

<script>
import DOMPurify from 'dompurify';
import { setActivePinia } from 'pinia';
import pinia from '../store/index';
import { useChatStore } from '../store/chatStore.js';
import { setFreedataMessageAsRead, getFreedataMessages, getFreedataMessageLog } from '../js/api.js';
import { displayToast, escapeHtml } from '../js/popupHandler.js';
import { loadBeaconData, parseServerTimestamp } from '../js/messagesHandler.js';
import { ref } from 'vue';
import { useIsMobile } from '../js/mobile_devices.js';
const { isMobile } = useIsMobile(720);


setActivePinia(pinia);

const chat = useChatStore(pinia);
const newChatCall = ref(null);
// the last saved message log: fileName, totalMessages, picked (saved where
// the user chose in the save dialog)
const messageLog = ref(null);
const savingMessageLog = ref(false);

function messageLogFileName() {
  const pad = (n) => String(n).padStart(2, '0');
  const d = new Date();
  return `freedata-message-log_${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}`
    + `-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}.csv`;
}

// Saves the message log (CSV, from the server) on this computer. Where the
// browser has a save dialog (Chrome and Edge, on 127.0.0.1, localhost or
// HTTPS), it asks for the folder and file name first, while the click
// still allows it. Other browsers download the file: they save it in the
// download folder, or ask where if set to. Server errors are shown as
// toast by getFreedataMessageLog.
async function saveMessageLog() {
  const suggestedName = messageLogFileName();
  let handle = null;
  if (window.showSaveFilePicker) {
    try {
      handle = await window.showSaveFilePicker({
        suggestedName,
        types: [{ description: 'CSV', accept: { 'text/csv': ['.csv'] } }],
      });
    } catch (error) {
      if (error?.name === 'AbortError') return; // the user cancelled
      handle = null; // no dialog after all: download instead
    }
  }

  savingMessageLog.value = true;
  try {
    const log = await getFreedataMessageLog();
    if (!log) return;
    if (handle) {
      const writable = await handle.createWritable();
      await writable.write(log.blob);
      await writable.close();
    } else {
      const url = URL.createObjectURL(log.blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = suggestedName;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    }
    messageLog.value = {
      fileName: handle ? handle.name : suggestedName,
      totalMessages: log.totalMessages,
      picked: Boolean(handle),
    };
  } catch (error) {
    displayToast('danger', 'bi-exclamation-octagon', `Message log not saved: ${escapeHtml(error?.message || String(error))}`, 8000);
  } finally {
    savingMessageLog.value = false;
  }
}

function chatSelected(callsign) {
  chat.selectedCallsign = callsign.toUpperCase();
  chat.triggerScrollToBottom();
  loadBeaconData(callsign);
  setMessagesAsRead(callsign);
}


import { watch } from 'vue';

watch(isMobile, (newVal) => {
  console.log("isMobile changed to:", newVal);
});



async function setMessagesAsRead(callsign) {
  const messages = chat.sorted_chat_list[callsign];
  if (messages) {
    messages.forEach((message) => {
      if (message.is_read === false) {
        setFreedataMessageAsRead(message.id);
        message.is_read = true;
      }
    });

    // Delay the execution of getFreedataMessages by 500 milliseconds
    setTimeout(() => {
      getFreedataMessages();
    }, 500);
  }
}

function getDateTime(input) {
  // Unix seconds or ISO 8601 (UTC, possibly without timezone designator)
  const date = parseServerTimestamp(input);

  const now = new Date();
  const isSameDay = date.getDate() === now.getDate() &&
                    date.getMonth() === now.getMonth() &&
                    date.getFullYear() === now.getFullYear();

  if (isSameDay) {
    // Use the browser's locale to format time only
    return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  } else {
    // Use the browser's locale to format both date and time
    const datePart = date.toLocaleDateString(undefined, { day: '2-digit', month: '2-digit', year: 'numeric' });
    //const timePart = date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    //return `${datePart} ${timePart}`;
    return `${datePart}`;
  }
}







function newChat() {
  let callsign = newChatCall.value;
  callsign = callsign.toUpperCase().trim();
  if (callsign === "") return;
  newChatCall.value = "";
}

function startNewChat() {
  chat.newChatCallsign = "";
  chat.newChatMessage = "Hi there! Nice to meet you!";
}

// Plain-text preview of the last message; "<file>" for attachment-only messages
function previewBody(body) {
  const text = body
    ? (DOMPurify.sanitize(body, { ALLOWED_TAGS: [], RETURN_DOM: true }).textContent || '').trim()
    : '';
  if (!text) return '<file>';
  return text.length > 35 ? text.substring(0, 35) + '...' : text;
}

export default {
  setup() {
    return {
      chat, newChatCall, chatSelected, setMessagesAsRead, getDateTime, newChat, startNewChat, previewBody,
      messageLog, savingMessageLog, saveMessageLog,
    };
  }
};
</script>
