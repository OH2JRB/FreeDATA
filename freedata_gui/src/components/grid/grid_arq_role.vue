<script setup>
import { computed } from "vue";
import { setActivePinia } from "pinia";
import pinia from "../../store/index";
setActivePinia(pinia);

import { useStateStore } from "../../store/stateStore.js";
const state = useStateStore(pinia);

const roleClass = computed(() => {
  if (state.arq_role === "ISS") return "text-bg-warning";
  if (state.arq_role === "IRS") return "text-bg-info";
  return "text-bg-light";
});
</script>

<template>
  <div
    class="arq-role-widget border rounded w-100 h-100"
    :class="roleClass"
  >
    <div
      v-if="state.arq_role"
      class="arq-role-lines"
    >
      <strong class="arq-role-name">{{ state.arq_role }}</strong>
      <span class="arq-role-label">
        {{ state.arq_role === 'ISS' ? $t('grid.arq_role_sending_to') : $t('grid.arq_role_receiving_from') }}
      </span>
      <strong class="arq-role-call font-monospace">{{ state.arq_remote_call || '-' }}</strong>
    </div>
    <div
      v-else
      class="arq-role-lines text-body-secondary"
    >
      <span class="arq-role-label">ARQ</span>
      <span class="arq-role-idle">{{ $t('grid.arq_role_idle') }}</span>
    </div>
  </div>
</template>

<style scoped>
/* Size container like the clock widget: the text follows the widget size */
.arq-role-widget {
  container-type: size;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}

.arq-role-lines {
  display: flex;
  flex-direction: column;
  align-items: center;
  line-height: 1.15;
  text-align: center;
  font-size: clamp(10px, min(22cqh, 11cqw), 300px);
}

.arq-role-name {
  font-size: 1.4em;
}

.arq-role-label {
  font-size: 0.6em;
}

.arq-role-idle {
  font-size: 0.8em;
}
</style>
