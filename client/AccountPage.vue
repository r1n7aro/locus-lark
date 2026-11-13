<script setup lang="ts">
// Settings tab of the lark plugin: the app Locus signs in with, the user's
// sign-in and its permissions. Every step runs through the plugin's
// `lark_auth` tool (`call`); the page itself never sees a secret, except the
// App Secret the user types into PluginSecretField, which goes straight to
// the keychain. A pending device flow shows its link and code while one
// `wait` call runs; leaving the page stops waiting (the link stays valid and
// shows again on return).
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { PluginSecretField } from "@locus/plugin-runtime";
import { BaseButton, BaseCheckbox, BaseSegmented } from "@locus/components";

const props = defineProps({
  t: { type: Function, required: true },
  call: { type: Function, required: true },
  settings: { type: Object, required: true },
  appSecret: { type: Object, required: true },
  userToken: { type: Object, required: true },
  changes: { type: Object, required: true },
  locale: { type: String, default: "en" },
});

const t = (key: string, ...args: unknown[]) => props.t(key, ...args) as string;
const call = props.call as (action: string, args?: Record<string, unknown>, signal?: AbortSignal) => Promise<any>;
const settings = props.settings as Record<string, any>;

const status = ref<any>(null);
const catalog = ref<any>(null);
const loadError = ref<string | null>(null);
const error = ref<string | null>(null);
const notice = ref<string | null>(null);
const busy = ref(false);
const waiting = ref(false);
const editingApp = ref(false);
const avatarFailed = ref(false);
const selectedDomains = reactive<Record<string, boolean>>({});
const selectedOptional = reactive<Record<string, boolean>>({});
let waitAbort: AbortController | null = null;
let disposed = false;

const pending = computed(() => status.value?.pending ?? null);
const appReady = computed(() => Boolean(status.value?.appReady));
const signedIn = computed(() => Boolean(status.value?.signedIn));
const showAppForm = computed(() => editingApp.value || (!appReady.value && !pending.value));
const brandOptions = computed(() => [
  { value: "feishu", label: t("brand.feishu") },
  { value: "lark", label: t("brand.lark") },
]);
const userName = computed(() => status.value?.user?.name || "");
const scopeCount = computed(() => String(status.value?.scope ?? "").split(/\s+/).filter(Boolean).length);
const renewUntil = computed(() => formatDate(status.value?.refreshExpiresAt));
const requestable = computed(() => {
  const domains = Object.keys(selectedDomains).filter((id) => selectedDomains[id]
    && !catalog.value?.domains?.find((item: any) => item.id === id)?.granted);
  const optional = Object.keys(selectedOptional).filter((id) => selectedOptional[id]
    && !catalog.value?.optional?.find((item: any) => item.id === id)?.granted);
  return { domains, optional };
});

function formatDate(seconds: unknown): string {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value <= 0) return "";
  return new Date(value * 1000).toLocaleDateString(props.locale === "zh" ? "zh-CN" : undefined);
}

function message(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

async function refresh() {
  try {
    const [nextStatus, nextCatalog] = await Promise.all([call("status"), call("catalog")]);
    if (disposed) return;
    status.value = nextStatus;
    catalog.value = nextCatalog;
    loadError.value = null;
    for (const item of nextCatalog.domains ?? []) selectedDomains[item.id] = Boolean(item.granted);
    for (const item of nextCatalog.optional ?? []) selectedOptional[item.id] = Boolean(item.granted);
    if (nextStatus.pending && !waiting.value) void waitForConfirmation(nextStatus.pending);
  } catch (cause) {
    if (!disposed) loadError.value = message(cause);
  }
}

/** Runs one action, then refreshes; a started flow is then waited for. */
async function run(action: string, args: Record<string, unknown> = {}) {
  if (busy.value) return;
  busy.value = true;
  error.value = null;
  notice.value = null;
  try {
    const reply = await call(action, args);
    if (action === "register" || action === "login") {
      status.value = { ...(status.value ?? {}), pending: { kind: reply.kind, url: reply.url, userCode: reply.userCode,
        expiresAt: Date.now() / 1000 + Number(reply.expiresIn || 0) } };
      void waitForConfirmation(status.value.pending);
    } else {
      await refresh();
    }
  } catch (cause) {
    error.value = message(cause);
  } finally {
    busy.value = false;
  }
}

async function waitForConfirmation(flow: any) {
  if (waiting.value) return;
  waiting.value = true;
  waitAbort = new AbortController();
  try {
    const seconds = Math.max(5, Math.min(840, Math.round(Number(flow.expiresAt || 0) - Date.now() / 1000) + 5));
    const reply = await call("wait", { timeoutSeconds: seconds }, waitAbort.signal);
    if (disposed) return;
    if (reply.done) {
      notice.value = t(`status.done.${reply.done}`);
      editingApp.value = false;
    }
  } catch (cause) {
    if (!disposed && !waitAbort?.signal.aborted) error.value = message(cause);
  } finally {
    waiting.value = false;
    waitAbort = null;
    if (!disposed) await refresh();
  }
}

async function cancelFlow() {
  waitAbort?.abort();
  await run("cancel");
}

function signIn() {
  void run("login", { open: true });
}

function requestSelected() {
  const { domains, optional } = requestable.value;
  if (!domains.length && !optional.length) {
    notice.value = t("perm.nothing");
    return;
  }
  // Permissions granted before are kept by the tool.
  void run("login", { open: true, domains, optional });
}

/** Removing the app takes a second click on the same button. */
const confirmingRemove = ref(false);
function removeApp() {
  if (!confirmingRemove.value) {
    confirmingRemove.value = true;
    return;
  }
  confirmingRemove.value = false;
  void run("forget_app");
}

function updateAppId(event: Event) {
  settings.appId = (event.target as HTMLInputElement).value.trim();
}

watch(() => (props.changes as any).value, () => {
  if (!busy.value && !waiting.value) void refresh();
});
// Entering an App ID and Secret by hand makes the app ready.
watch(() => [settings.appId, (props.appSecret as any).value?.configured], () => {
  if (!busy.value && !waiting.value) void refresh();
});

onMounted(() => void refresh());
onBeforeUnmount(() => {
  disposed = true;
  waitAbort?.abort();
});
</script>

<template>
  <div class="lark-account">
    <div v-if="!status" class="plugin-settings-card">
      <div class="lark-status" :class="{ error: loadError }">{{ loadError || t("status.loading") }}</div>
    </div>

    <template v-else>
      <div v-if="error || notice" class="lark-banner" :class="{ error }" role="status">{{ error || notice }}</div>

      <!-- A device flow waiting for the user. -->
      <div v-if="pending" class="plugin-settings-card lark-pending">
        <div class="plugin-setting-row">
          <div class="plugin-setting-text">
            <div class="plugin-setting-label">{{ t(pending.kind === "register" ? "pending.register" : "pending.login") }}</div>
            <a class="lark-link" :href="pending.url" target="_blank" rel="noopener noreferrer">{{ pending.url }}</a>
            <div class="plugin-setting-desc">
              <span v-if="pending.userCode" class="lark-code">{{ t("pending.code", pending.userCode) }}</span>
              {{ t("pending.waiting") }}
            </div>
          </div>
          <div class="plugin-setting-control">
            <a class="lark-open" :href="pending.url" target="_blank" rel="noopener noreferrer">{{ t("pending.open") }}</a>
            <BaseButton size="sm" :disabled="busy" @click="cancelFlow">{{ t("pending.cancel") }}</BaseButton>
          </div>
        </div>
      </div>

      <!-- App -->
      <h4 class="lark-heading">{{ t("app.title") }}</h4>
      <div class="plugin-settings-card">
        <div class="plugin-setting-row">
          <div class="plugin-setting-text">
            <template v-if="appReady">
              <div class="plugin-setting-label">
                <span class="lark-mono">{{ status.appId }}</span>
                <span class="plugin-setting-badge">{{ t(`brand.${status.brand}`) }}</span>
              </div>
              <div class="plugin-setting-desc">{{ t(status.appSource === "registered" ? "app.registered" : "app.custom") }}</div>
            </template>
            <div v-else class="plugin-setting-desc">{{ t("app.none") }}</div>
          </div>
          <div class="plugin-setting-control">
            <template v-if="appReady">
              <BaseButton v-if="!editingApp" size="sm" :disabled="busy || waiting" @click="editingApp = true">{{ t("app.change") }}</BaseButton>
              <BaseButton size="sm" variant="danger" :disabled="busy || waiting" :title="confirmingRemove ? t('app.removeConfirm') : undefined"
                @click="removeApp" @mouseleave="confirmingRemove = false" @blur="confirmingRemove = false">
                {{ confirmingRemove ? t("app.removeAgain") : t("app.remove") }}
              </BaseButton>
            </template>
            <BaseButton v-else size="sm" variant="primary" :disabled="busy || waiting" @click="run('register', { open: true })">
              {{ t("app.create") }}
            </BaseButton>
          </div>
        </div>

        <template v-if="showAppForm">
          <div class="plugin-setting-row">
            <div class="plugin-setting-text">
              <div class="plugin-setting-label">{{ appReady ? t("app.change") : t("app.useExisting") }}</div>
              <div class="plugin-setting-desc">{{ t("app.manualNote") }}</div>
            </div>
            <div class="plugin-setting-control">
              <BaseButton v-if="appReady" size="sm" variant="primary" :disabled="busy || waiting" @click="run('register', { open: true })">
                {{ t("app.create") }}
              </BaseButton>
              <BaseButton v-if="editingApp" size="sm" @click="editingApp = false">{{ t("app.close") }}</BaseButton>
            </div>
          </div>
          <div class="plugin-setting-row">
            <div class="plugin-setting-label">{{ t("app.brandLabel") }}</div>
            <div class="plugin-setting-control">
              <BaseSegmented size="sm" :options="brandOptions" :model-value="settings.brand" @update:model-value="settings.brand = $event" />
            </div>
          </div>
          <div class="plugin-setting-row">
            <div class="plugin-setting-label">{{ t("app.idLabel") }}</div>
            <div class="plugin-setting-control fill">
              <input class="lark-input" :value="settings.appId" placeholder="cli_…" spellcheck="false" autocomplete="off" @change="updateAppId" />
            </div>
          </div>
          <div class="plugin-setting-row">
            <div class="plugin-setting-label">{{ t("app.secretLabel") }}</div>
            <div class="plugin-setting-control fill">
              <PluginSecretField name="appSecret" />
            </div>
          </div>
        </template>
      </div>

      <!-- Account -->
      <h4 class="lark-heading">{{ t("account.title") }}</h4>
      <div class="plugin-settings-card">
        <div class="plugin-setting-row">
          <div class="plugin-setting-text">
            <template v-if="signedIn">
              <div class="plugin-setting-label">
                <img v-if="status.user?.avatarUrl && !avatarFailed" class="lark-avatar" :src="status.user.avatarUrl" alt=""
                  @error="avatarFailed = true" />
                {{ userName ? t("account.signedIn", userName) : t("account.unknownUser") }}
              </div>
              <div class="plugin-setting-desc">{{ t("account.scopes", scopeCount, renewUntil) }}</div>
            </template>
            <div v-else class="plugin-setting-desc">{{ appReady ? t("account.signedOut") : t("account.needsApp") }}</div>
          </div>
          <div class="plugin-setting-control">
            <BaseButton v-if="signedIn" size="sm" :disabled="busy || waiting" @click="run('logout')">{{ t("account.signOut") }}</BaseButton>
            <BaseButton v-else size="sm" variant="primary" :disabled="!appReady || busy || waiting" @click="signIn">{{ t("account.signIn") }}</BaseButton>
          </div>
        </div>
      </div>

      <!-- Permissions -->
      <template v-if="appReady && catalog">
        <h4 class="lark-heading">{{ t("perm.title") }}</h4>
        <div class="plugin-settings-card">
          <div class="plugin-setting-row wide">
            <div class="plugin-setting-text">
              <div class="plugin-setting-desc">{{ t("perm.desc") }}</div>
            </div>
            <div class="lark-perm-grid">
              <div class="lark-perm-group">{{ t("perm.domains") }}</div>
              <label v-for="item in catalog.domains" :key="item.id" class="lark-perm">
                <BaseCheckbox :model-value="Boolean(selectedDomains[item.id])" :disabled="item.granted"
                  @update:model-value="selectedDomains[item.id] = $event" />
                <span>{{ t(`domain.${item.id}`) }}</span>
                <span v-if="item.granted" class="lark-granted">{{ t("perm.granted") }}</span>
              </label>
              <div class="lark-perm-group">{{ t("perm.optional") }}</div>
              <label v-for="item in catalog.optional" :key="item.id" class="lark-perm">
                <BaseCheckbox :model-value="Boolean(selectedOptional[item.id])" :disabled="item.granted"
                  @update:model-value="selectedOptional[item.id] = $event" />
                <span>{{ t(`optional.${item.id}`) }}</span>
                <span v-if="item.granted" class="lark-granted">{{ t("perm.granted") }}</span>
              </label>
            </div>
            <div class="lark-actions">
              <BaseButton size="sm" variant="primary" :disabled="busy || waiting || (!requestable.domains.length && !requestable.optional.length)"
                @click="requestSelected">{{ t("perm.request") }}</BaseButton>
            </div>
          </div>
        </div>
      </template>
    </template>
  </div>
</template>

<style scoped>
.lark-account {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.lark-heading {
  margin: 10px 0 0;
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 500;
}

.lark-status {
  padding: 14px 16px;
  color: var(--text-secondary);
  font-size: 13px;
}

.lark-status.error,
.lark-banner.error {
  color: var(--status-danger-fg);
}

.lark-banner {
  padding: 8px 12px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.5;
  word-break: break-word;
}

.lark-pending {
  border-color: var(--accent-border, var(--border-color));
}

.lark-link {
  overflow: hidden;
  color: var(--accent-color);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.lark-open {
  color: var(--accent-color);
  font-size: 12px;
  text-decoration: none;
}

.lark-code {
  margin-right: 6px;
  color: var(--text-color);
  font-family: var(--font-mono, monospace);
}

.lark-mono {
  font-family: var(--font-mono, monospace);
}

.lark-avatar {
  width: 18px;
  height: 18px;
  border-radius: 50%;
}

.lark-input {
  width: 100%;
  height: 30px;
  padding: 0 10px;
  border: 1px solid var(--border-color);
  border-radius: 6px;
  background: var(--input-bg);
  color: var(--text-color);
  font-size: 13px;
  font-family: var(--font-mono, monospace);
  outline: none;
  box-sizing: border-box;
}

.lark-input:focus {
  border-color: var(--accent-border);
}

.lark-perm-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 6px 16px;
}

.lark-perm-group {
  grid-column: 1 / -1;
  margin-top: 4px;
  color: var(--text-secondary);
  font-size: 11px;
}

.lark-perm {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-color);
  font-size: 12px;
  cursor: pointer;
}

.lark-granted {
  color: var(--status-good-fg);
  font-size: 11px;
}

.lark-actions {
  display: flex;
  justify-content: flex-end;
}
</style>
