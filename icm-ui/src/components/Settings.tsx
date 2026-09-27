import { useCallback, useEffect, useState } from "react";
import { deleteSetting, getSetting, healthCheck, setSetting } from "../api";
import { Button, Callout, Card, CardHeader, Icon, PageHeader, Spinner } from "./ui";
import { cx } from "./ui/cx";

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
    <div className="animate-in">
      <PageHeader
        title="Settings"
        description="Connection, AI and currency settings. They are stored by the local engine, not in your browser."
        actions={
          <Button variant="primary" icon={saved ? "check" : undefined} onClick={save}>
            {saved ? "Saved" : "Save settings"}
          </Button>
        }
      />

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="space-y-5">
          <Card>
            <CardHeader icon="globe" title="Connection" description="Where this app finds the calculation engine." />
            <div className="space-y-4 px-5 py-4">
              <div className="flex items-center gap-3 rounded-xl border border-line bg-surface-2/60 px-3.5 py-2.5">
                <span className={cx(
                  "h-2 w-2 shrink-0 rounded-full",
                  health === "ok" && "bg-success shadow-[0_0_0_3px_var(--c-success-soft)]",
                  health === "error" && "bg-danger shadow-[0_0_0_3px_var(--c-danger-soft)]",
                  health === "checking" && "animate-pulse bg-warning",
                )} />
                <span className="text-[13px] text-ink">
                  Engine API: {health === "ok" ? "Connected" : health === "error" ? "Not reachable" : "Checking..."}
                </span>
                <Button
                  size="sm"
                  variant="ghost"
                  icon="refresh"
                  className="ml-auto"
                  onClick={() => { setHealth("checking"); healthCheck().then((ok) => setHealth(ok ? "ok" : "error")); }}
                >
                  Retry
                </Button>
              </div>
              <label className="block">
                <span className="field-label">Engine API URL <span className="font-normal text-ink-3">(optional)</span></span>
                <input
                  type="text"
                  value={apiBase}
                  onChange={(e) => setApiBase(e.target.value)}
                  placeholder="http://localhost:8000"
                  className="w-full font-mono"
                />
                <span className="field-hint block">
                  Leave blank to use the default ({import.meta.env.VITE_API_BASE || "http://localhost:8000"}).
                </span>
              </label>
            </div>
          </Card>

          <Card>
            <CardHeader icon="sparkles" title="AI plan builder" description="Needed only to draft plans from plain English." />
            <div className="px-5 py-4">
              <label className="block">
                <span className="field-label">Anthropic API key</span>
                <div className="relative">
                  <input
                    type={showKey ? "text" : "password"}
                    value={showKey ? apiKey : maskedKey || apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    onFocus={() => setShowKey(true)}
                    placeholder="sk-ant-..."
                    className="w-full pr-16 font-mono"
                  />
                  <button
                    onClick={() => setShowKey(!showKey)}
                    type="button"
                    className="absolute right-2 top-1/2 -translate-y-1/2 rounded px-1.5 py-0.5 text-[12px] font-medium text-ink-2 hover:bg-surface-2 hover:text-ink"
                  >
                    {showKey ? "Hide" : "Show"}
                  </button>
                </div>
                <span className="field-hint block">
                  Stored by the local engine and sent to Anthropic with each plan request. Never kept in the browser.
                </span>
              </label>
            </div>
          </Card>

          <Card>
            <CardHeader icon="coins" title="Currency and rounding" description="How amounts are converted and rounded for display." />
            <div className="grid gap-4 px-5 py-4 sm:grid-cols-2">
              <label className="block">
                <span className="field-label">Exchange rates (JSON)</span>
                <input
                  type="text"
                  value={exchangeRates}
                  onChange={(e) => setExchangeRates(e.target.value)}
                  placeholder='{"CAD": "1.35", "EUR": "0.92"}'
                  className="w-full font-mono"
                />
                <span className="field-hint block">1 USD = X units of each currency. Display conversion only.</span>
              </label>
              <label className="block">
                <span className="field-label">Rounding mode</span>
                <select value={roundingMode} onChange={(e) => setRoundingMode(e.target.value)} className="w-full">
                  <option value="half-up">Half-up (standard)</option>
                  <option value="floor">Floor (always down)</option>
                  <option value="ceil">Ceil (always up)</option>
                  <option value="none">None (exact precision)</option>
                </select>
              </label>
            </div>
          </Card>
        </div>

        <div className="space-y-5">
          <Card>
            <CardHeader icon="download" title="Software updates" description="Current version: 0.1.0" />
            <div className="space-y-3 px-5 py-4">
              {updateStatus.status === "available" && updateStatus.info && (
                <Callout
                  tone="info"
                  title={`Update available: v${updateStatus.info.version}`}
                >
                  {updateStatus.info.release_notes && (
                    <p className="whitespace-pre-wrap">{updateStatus.info.release_notes}</p>
                  )}
                  <div className="mt-2 flex flex-wrap gap-2">
                    <Button size="sm" variant="primary" onClick={applyUpdate} disabled={updateApplying} loading={updateApplying}>
                      {updateApplying ? "Installing..." : "Update & restart"}
                    </Button>
                    <Button size="sm" onClick={skipUpdate}>Skip this version</Button>
                  </div>
                </Callout>
              )}

              {updateStatus.status === "checking" && (
                <p className="flex items-center gap-2 text-[12.5px] text-ink-2"><Spinner size={12} /> Checking for updates...</p>
              )}
              {updateStatus.status === "idle" && updateStatus.message && (
                <p className="text-[12.5px] text-ink-2">{updateStatus.message}</p>
              )}
              {updateStatus.status === "error" && (
                <p className="text-[12.5px] text-danger-ink">{updateStatus.message || "Update check failed"}</p>
              )}
              {updateStatus.status === "installing" && (
                <p className="text-[12.5px] text-ink-2">Installing update — the app will restart shortly...</p>
              )}

              <Button
                size="sm"
                icon="refresh"
                onClick={checkForUpdates}
                loading={updateChecking}
                disabled={updateChecking || updateStatus.status === "downloading" || updateStatus.status === "installing"}
              >
                {updateChecking ? "Checking..." : "Check for updates"}
              </Button>
            </div>
          </Card>

          <Card>
            <CardHeader icon="shield" title="Privacy and security" />
            <ul className="space-y-2.5 px-5 py-4 text-[12.5px] text-ink-2">
              <li className="flex gap-2"><Icon name="check" size={14} className="mt-0.5 shrink-0 text-success-ink" />Your API key is stored by the local engine and sent to Anthropic per request. It is never stored in your browser.</li>
              <li className="flex gap-2"><Icon name="check" size={14} className="mt-0.5 shrink-0 text-success-ink" />Non-sensitive settings (the API URL, your theme) are cached in the browser for convenience.</li>
              <li className="flex gap-2"><Icon name="check" size={14} className="mt-0.5 shrink-0 text-success-ink" />Clear your browser data, or delete settings via the API, to remove stored keys.</li>
            </ul>
          </Card>
        </div>
      </div>
    </div>
  );
}
