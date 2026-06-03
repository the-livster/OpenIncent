import { useCallback, useEffect, useState } from "react";
import { deleteSetting, getSetting, healthCheck, setSetting } from "../api";

const KEY_ANTHROPIC = "anthropic_api_key";
const KEY_API_BASE = "api_base";

export default function Settings() {
  const [apiKey, setApiKey] = useState("");
  const [apiBase, setApiBase] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [saved, setSaved] = useState(false);
  const [health, setHealth] = useState<"checking" | "ok" | "error">("checking");

  // Load settings from API (fallback to localStorage)
  useEffect(() => {
    async function load() {
      const key = await getSetting(KEY_ANTHROPIC);
      const base = await getSetting(KEY_API_BASE);
      if (key !== null) setApiKey(key);
      else setApiKey(localStorage.getItem("icm_anthropic_key") ?? "");
      if (base !== null) setApiBase(base);
      else setApiBase(localStorage.getItem("icm_api_base") ?? "");
    }
    load();
  }, []);

  useEffect(() => {
    healthCheck().then((ok) => setHealth(ok ? "ok" : "error"));
  }, []);

  const save = useCallback(async () => {
    // Persist to API
    if (apiKey.trim()) {
      await setSetting(KEY_ANTHROPIC, apiKey.trim());
    } else {
      await deleteSetting(KEY_ANTHROPIC);
    }

    if (apiBase.trim()) {
      await setSetting(KEY_API_BASE, apiBase.trim());
    } else {
      await deleteSetting(KEY_API_BASE);
    }

    // Also keep localStorage as fallback
    if (apiKey.trim()) {
      localStorage.setItem("icm_anthropic_key", apiKey.trim());
    } else {
      localStorage.removeItem("icm_anthropic_key");
    }
    if (apiBase.trim()) {
      localStorage.setItem("icm_api_base", apiBase.trim());
    } else {
      localStorage.removeItem("icm_api_base");
    }

    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
    healthCheck().then((ok) => setHealth(ok ? "ok" : "error"));
  }, [apiKey, apiBase]);

  const maskedKey = apiKey
    ? `${apiKey.slice(0, 7)}${"\u2022".repeat(Math.max(0, apiKey.length - 11))}${apiKey.slice(-4)}`
    : "";

  return (
    <div className="max-w-2xl mx-auto space-y-6 animate-in">
      <div className="card p-6 space-y-5">
        <div>
          <h2 className="text-lg font-semibold text-ink">Settings</h2>
          <p className="text-sm text-ink2 mt-1">
            Configure your API keys and connection settings. Settings are persisted on the server.
          </p>
        </div>

        {/* API Connection Status */}
        <div className="flex items-center gap-3 px-3 py-2.5 rounded-lg bg-soft">
          <div className={`w-2 h-2 rounded-full ${
            health === "ok" ? "bg-success" : health === "error" ? "bg-danger" : "bg-warn animate-pulse"
          }`} />
          <span className="text-sm text-ink">
            Engine API: {health === "ok" ? "Connected" : health === "error" ? "Not reachable" : "Checking..."}
          </span>
          <button
            onClick={() => { setHealth("checking"); healthCheck().then((ok) => setHealth(ok ? "ok" : "error")); }}
            className="ml-auto text-xs text-accent hover:text-brand-300 cursor-pointer transition-colors"
          >
            Retry
          </button>
        </div>

        {/* Anthropic API Key */}
        <div>
          <label className="block text-xs font-medium text-ink2 mb-1.5">
            Anthropic API Key
          </label>
          <p className="text-xs text-ink2 mb-2">
            Required for AI Plan Builder. Your key is stored server-side and sent to Anthropic per-request.
          </p>
          <div className="relative">
            <input
              type={showKey ? "text" : "password"}
              value={showKey ? apiKey : maskedKey || apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              onFocus={() => setShowKey(true)}
              placeholder="sk-ant-..."
              className="
                w-full px-3 py-2 pr-16 rounded-lg text-sm font-mono
                bg-soft border border-line
                text-ink placeholder:text-ink2
                focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/10
                transition-all
              "
            />
            <button
              onClick={() => setShowKey(!showKey)}
              type="button"
              className="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-ink2 hover:text-ink cursor-pointer transition-colors px-1"
            >
              {showKey ? "Hide" : "Show"}
            </button>
          </div>
        </div>

        {/* API Base URL */}
        <div>
          <label className="block text-xs font-medium text-ink2 mb-1.5">
            Engine API URL <span className="text-ink2">(optional)</span>
          </label>
          <input
            type="text"
            value={apiBase}
            onChange={(e) => setApiBase(e.target.value)}
            placeholder="http://localhost:8000"
            className="
              w-full px-3 py-2 rounded-lg text-sm font-mono
              bg-soft border border-line
              text-ink placeholder:text-ink2
              focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/10
              transition-all
            "
          />
          <p className="text-xs text-ink2 mt-1">
            Leave blank to use the default ({import.meta.env.VITE_API_BASE || "http://localhost:8000"}).
          </p>
        </div>

        {/* Save */}
        <button
          onClick={save}
          className="
            w-full py-2.5 rounded-lg font-medium text-sm
            bg-gradient-to-r from-accent to-accent-ink
            text-white
            hover:from-accent hover:to-accent
            transition-all cursor-pointer
            shadow-sm
          "
        >
          {saved ? "\u2713 Saved!" : "Save Settings"}
        </button>
      </div>

      {/* Info */}
      <div className="card p-4 text-xs text-ink2 space-y-2">
        <div className="font-semibold text-ink2">Privacy &amp; Security</div>
        <ul className="list-disc list-inside space-y-1">
          <li>Your API key is stored server-side in the local database and sent to Anthropic per-request.</li>
          <li>Settings are also cached in your browser's localStorage as a fallback.</li>
          <li>Clear your browser data or delete settings via the API to remove stored keys.</li>
        </ul>
      </div>
    </div>
  );
}
