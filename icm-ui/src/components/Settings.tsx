import { useCallback, useEffect, useState } from "react";
import { deleteSetting, getSetting, healthCheck, setSetting } from "../api";

const KEY_ANTHROPIC = "anthropic_api_key";
const KEY_API_BASE = "api_base";
const KEY_EXCHANGE_RATES = "exchange_rates";
const KEY_ROUNDING = "rounding_mode";

function getBase(): string {
  const stored = localStorage.getItem("icm_api_base");
  return stored || "";
}

interface UpdateStatus {
  status: string;
  info?: { version: string; release_notes: string };
  message?: string;
}

export default function Settings() {
  const [apiKey, setApiKey] = useState("");
  const [apiBase, setApiBase] = useState("");
  const [exchangeRates, setExchangeRates] = useState("");
  const [roundingMode, setRoundingMode] = useState("half-up");
  const [showKey, setShowKey] = useState(false);
  const [saved, setSaved] = useState(false);
  const [health, setHealth] = useState<"checking" | "ok" | "error">("checking");

  // Update state
  const [updateStatus, setUpdateStatus] = useState<UpdateStatus>({ status: "idle" });
  const [updateChecking, setUpdateChecking] = useState(false);
  const [updateApplying, setUpdateApplying] = useState(false);

  // Poll update status
  useEffect(() => {
    let cancelled = false;
    async function poll() {
      try {
        const res = await fetch(`${getBase()}/v1/update/status`);
        if (!cancelled && res.ok) {
          const data = await res.json();
          setUpdateStatus(data as UpdateStatus);
        }
      } catch { /* desktop-only endpoint */ }
    }
    poll();
    const interval = setInterval(poll, 30000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  // Load settings from API (fallback to localStorage)
  useEffect(() => {
    async function load() {
      const key = await getSetting(KEY_ANTHROPIC);
      const base = await getSetting(KEY_API_BASE);
      const rates = await getSetting(KEY_EXCHANGE_RATES);
      const rmode = await getSetting(KEY_ROUNDING);
      if (key !== null) setApiKey(key);
      if (base !== null) setApiBase(base);
      else setApiBase(localStorage.getItem("icm_api_base") ?? "");
      if (rates !== null) setExchangeRates(rates);
      if (rmode !== null) setRoundingMode(rmode);
    }
    load();
  }, []);

  useEffect(() => {
    healthCheck().then((ok) => setHealth(ok ? "ok" : "error"));
  }, []);

  const checkForUpdates = useCallback(async () => {
    setUpdateChecking(true);
    try {
      const res = await fetch(`${getBase()}/v1/update/check`, { method: "POST" });
      if (res.ok) {
        const data = await res.json();
        setUpdateStatus(data as UpdateStatus);
      }
    } catch { /* ignore */ }
    setUpdateChecking(false);
  }, []);

  const applyUpdate = useCallback(async () => {
    setUpdateApplying(true);
    try {
      const res = await fetch(`${getBase()}/v1/update/apply`, { method: "POST" });
      if (res.ok) {
        const data = await res.json();
        setUpdateStatus(data as UpdateStatus);
      }
    } catch { /* ignore */ }
    setUpdateApplying(false);
  }, []);

  const skipUpdate = useCallback(async () => {
    try {
      await fetch(`${getBase()}/v1/update/skip`, { method: "POST" });
      setUpdateStatus({ status: "idle" });
    } catch { /* ignore */ }
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

    if (exchangeRates.trim()) {
      await setSetting(KEY_EXCHANGE_RATES, exchangeRates.trim());
    } else {
      await deleteSetting(KEY_EXCHANGE_RATES);
    }

    if (roundingMode && roundingMode !== "half-up") {
      await setSetting(KEY_ROUNDING, roundingMode);
    } else {
      await deleteSetting(KEY_ROUNDING);
    }

    // Also keep api_base in localStorage as fallback (not a secret)
    if (apiBase.trim()) {
      localStorage.setItem("icm_api_base", apiBase.trim());
    } else {
      localStorage.removeItem("icm_api_base");
    }

    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
    healthCheck().then((ok) => setHealth(ok ? "ok" : "error"));
  }, [apiKey, apiBase, exchangeRates, roundingMode]);

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

        {/* Exchange Rates */}
        <div>
          <label className="block text-xs font-medium text-ink2 mb-1.5">
            Exchange Rates (JSON)
          </label>
          <input
            type="text"
            value={exchangeRates}
            onChange={(e) => setExchangeRates(e.target.value)}
            placeholder='{"CAD": "1.35", "EUR": "0.92"}'
            className="w-full px-3 py-2 rounded-lg text-sm font-mono bg-soft border border-line text-ink placeholder:text-ink2 focus:outline-none focus:border-accent transition-all"
          />
          <p className="text-xs text-ink2 mt-1">
            Rates vs USD. 1 USD = X units of each currency. Used for multi-currency display conversion.
          </p>
        </div>

        {/* Rounding Mode */}
        <div>
          <label className="block text-xs font-medium text-ink2 mb-1.5">
            Rounding Mode
          </label>
          <select
            value={roundingMode}
            onChange={(e) => setRoundingMode(e.target.value)}
            className="w-full px-3 py-2 rounded-lg text-sm bg-soft border border-line text-ink focus:outline-none focus:border-accent transition-all"
          >
            <option value="half-up">Half-Up (standard)</option>
            <option value="floor">Floor (always down)</option>
            <option value="ceil">Ceil (always up)</option>
            <option value="none">None (exact precision)</option>
          </select>
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

      {/* Updates */}
      <div className="card p-6 space-y-4">
        <div>
          <h3 className="text-sm font-semibold text-ink">Software Updates</h3>
          <p className="text-xs text-ink2 mt-0.5">
            Current version: 0.1.0
          </p>
        </div>

        {updateStatus.status === "available" && updateStatus.info && (
          <div className="px-4 py-3 rounded-lg bg-accent/5 border border-accent/20 space-y-2">
            <p className="text-sm text-ink font-medium">
              Update available: v{updateStatus.info.version}
            </p>
            {updateStatus.info.release_notes && (
              <p className="text-xs text-ink2 whitespace-pre-wrap">{updateStatus.info.release_notes}</p>
            )}
            <div className="flex gap-2 pt-1">
              <button
                onClick={applyUpdate}
                disabled={updateApplying}
                className="px-3 py-1.5 rounded-lg text-xs font-medium bg-accent text-white hover:bg-ink transition-colors cursor-pointer"
              >
                {updateApplying ? "Installing..." : "Update & Restart"}
              </button>
              <button
                onClick={skipUpdate}
                className="px-3 py-1.5 rounded-lg text-xs font-medium bg-soft border border-line text-ink2 hover:text-ink transition-colors cursor-pointer"
              >
                Skip this version
              </button>
            </div>
          </div>
        )}

        {updateStatus.status === "checking" && (
          <p className="text-xs text-ink2">Checking for updates...</p>
        )}

        {updateStatus.status === "idle" && updateStatus.message && (
          <p className="text-xs text-ink2">{updateStatus.message}</p>
        )}

        {updateStatus.status === "error" && (
          <p className="text-xs text-danger">{updateStatus.message || "Update check failed"}</p>
        )}

        {updateStatus.status === "installing" && (
          <p className="text-xs text-ink2">Installing update — app will restart shortly...</p>
        )}

        <button
          onClick={checkForUpdates}
          disabled={updateChecking || updateStatus.status === "downloading" || updateStatus.status === "installing"}
          className="text-xs text-accent hover:text-ink cursor-pointer transition-colors"
        >
          {updateChecking ? "Checking..." : "Check for updates"}
        </button>
      </div>

      {/* Info */}
      <div className="card p-4 text-xs text-ink2 space-y-2">
        <div className="font-semibold text-ink2">Privacy &amp; Security</div>
        <ul className="list-disc list-inside space-y-1">
          <li>Your API key is stored server-side in the local database and sent to Anthropic per-request. It is never stored in your browser.</li>
          <li>Non-sensitive settings (API base URL) are cached in localStorage for convenience.</li>
          <li>Clear your browser data or delete settings via the API to remove stored keys.</li>
        </ul>
      </div>
    </div>
  );
}
