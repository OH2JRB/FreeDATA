import {
  eventDispatcher,
  stateDispatcher,
  connectionFailed,
  loadAllData,
} from "../js/eventHandler.js";
import { addDataToWaterfall } from "../js/waterfallHandler.js";
import { addDataToAudio } from "../js/audioStreamHandler.js";

// ----------------- init pinia stores -------------
import { setActivePinia } from "pinia";
import pinia from "../store/index";
setActivePinia(pinia);

import { useStateStore } from "../store/stateStore.js";
const state = useStateStore(pinia);

// While the modem runs, the server sends FFT rows several times a second. If
// none arrive for this long, the fft socket is closed and so reconnected: a
// socket the server has stopped sending to stays open and the waterfall
// freezes (e.g. after the browser was in the background).
const FFT_STALL_MS = 10000;
const FFT_CHECK_MS = 2000;

function connect(endpoint, dispatcher) {
  const { protocol, hostname, port } = window.location;
  const wsProtocol = protocol === "https:" ? "wss:" : "ws:";
  const adjustedPort = port === "8080" ? "5000" : port;
  const socket = new WebSocket(
    `${wsProtocol}//${hostname}:${adjustedPort}/${endpoint}`,
  );

  if (endpoint.includes("audio")) {
    socket.binaryType = "arraybuffer";
  }

  let lastMessage = Date.now();
  const watchdog =
    endpoint === "fft"
      ? setInterval(() => {
          if (
            socket.readyState === WebSocket.OPEN &&
            state.is_modem_running &&
            Date.now() - lastMessage > FFT_STALL_MS
          ) {
            console.warn("No FFT data, reconnecting the waterfall");
            socket.close();
          }
        }, FFT_CHECK_MS)
      : null;

  // handle opening
  socket.addEventListener("open", function () {
    console.log(`Connected to the WebSocket server: ${endpoint}`);
    lastMessage = Date.now();
    // The states socket defines the connection state. Load all data once
    // per (re)connect, not once for every socket.
    if (endpoint === "states") {
      state.modem_connection = "connected";
      loadAllData();
    }
  });

  // handle data
  socket.addEventListener("message", function (event) {
    lastMessage = Date.now();
    dispatcher(event.data);
  });

  // handle errors
  socket.addEventListener("error", function (event) {
    connectionFailed(endpoint, event);
  });

  // handle closing and reconnect
  socket.addEventListener("close", function (event) {
    console.log(`WebSocket connection closed: ${event.code}`);
    if (watchdog) clearInterval(watchdog);

    // Reconnect handler
    setTimeout(() => {
      connect(endpoint, dispatcher);
    }, 1000);
  });
}

// Initial connection attempts to endpoints
export function initConnections() {
  connect("states", stateDispatcher);
  connect("events", eventDispatcher);
  connect("fft", addDataToWaterfall);
  connect("audio_rx", addDataToAudio);
}
