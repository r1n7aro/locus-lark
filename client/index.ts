// Client entry of the lark plugin: its Settings tab (Plugins → 飞书 / Lark),
// where the user sets up the app, signs in and out, and grants permissions.
// It loads only while that tab is open. Everything that touches secrets runs
// in the plugin's `lark_auth` tool; the page sees replies without tokens.
import { ref } from "vue";
import AccountPage from "./AccountPage.vue";
import { messages } from "./locales";

const TOOL = "lark_auth";

export function apply(ctx: any) {
  const t = ctx.locale.register(messages);
  const settings = ctx.settings.bind(["brand", "appId"]);
  const appSecret = ctx.settings.secret("appSecret");
  const userToken = ctx.settings.secret("userToken");
  // Bumped when a setting changes elsewhere (a script signed in, a tool renewed the token).
  const changes = ref(0);
  ctx.settings.onChanged(() => {
    changes.value += 1;
  });

  /** Runs one action of `lark_auth`; resolves to its JSON reply, rejects with its message. */
  async function call(action: string, args: Record<string, unknown> = {}, signal?: AbortSignal) {
    const result = await ctx.tools.call(TOOL, { action, ...args }, { signal });
    let reply: any = null;
    try {
      reply = JSON.parse(result.text || "null");
    } catch {
      reply = null;
    }
    if (result.isError || !reply || typeof reply !== "object") {
      const error: any = new Error(reply?.error || result.text || `${TOOL} ${action} failed`);
      error.code = reply?.code ?? null;
      throw error;
    }
    return reply;
  }

  ctx.slots.register(
    { slot: "settings.plugin.page", id: "account" },
    AccountPage,
    { inject: () => ({ t, call, settings, appSecret, userToken, changes, locale: ctx.meta.locale }) },
  );
}
