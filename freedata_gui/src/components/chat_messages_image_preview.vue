<template>
  <div v-if="isImage && imageUrl">
    <img
      :src="imageUrl"
      alt="Image Preview"
      class="img-fluid border rounded-top w-100"
    >
  </div>
</template>

<script setup>
import { computed, ref, toRefs, watch } from 'vue';
import { attachmentData } from '../js/attachmentCache';

const props = defineProps(['attachment'])
const { attachment } = toRefs(props);

const isImage = computed(() => {
  const imageFormats = ["gif", "png", "jpg", "jpeg", "svg"];
  const extension = attachment.value.name.split(".").pop().toLowerCase();
  return imageFormats.includes(extension);
});

// The message list comes without attachment data: fetch an image's data by
// its hash (once per session, js/attachmentCache.js). An attachment that
// still carries its data (a single message loaded whole) uses it directly.
const data = ref(attachment.value.data || null);
watch(
  () => [attachment.value.hash_sha512, attachment.value.data, isImage.value],
  async ([hash, inline, image]) => {
    if (!image) return;
    if (inline) {
      data.value = inline;
      return;
    }
    const loaded = await attachmentData(hash);
    // the attachment may have changed while loading
    if (attachment.value.hash_sha512 === hash) data.value = loaded;
  },
  { immediate: true },
);

const imageUrl = computed(() => {
  return isImage.value && data.value ? `data:${attachment.value.type};base64,${data.value}` : "";
});
</script>
