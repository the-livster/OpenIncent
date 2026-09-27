import { useCallback, useEffect, useState } from "react";
import { listPlans, savePlan } from "../api";
import type { SavedPlan } from "../types";
import StageShell from "./StageShell";
import UploadZone from "./UploadZone";
import { Badge, Callout, Icon } from "./ui";

interface Props {
  plans: SavedPlan[];
  setPlans: (p: SavedPlan[]) => void;
  onNext: () => void;
  onBack: () => void;
  sample?: boolean;
  onChanged: () => void;
}

export default function StagePlans({ plans, setPlans, onNext, onBack, sample, onChanged }: Props) {
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (!sample) listPlans().then(setPlans).catch(() => setStatus("Could not load plans.")); }, [sample, setPlans]);

  const handleFiles = useCallback(async (files: File[]) => {
    if (!files.length || sample) return;
    onChanged();
    setBusy(true);
    setStatus(`Importing ${files.length} plan(s)...`);
    const existingIds = new Set(plans.map(p => p.id));
    let imported = 0;
    for (const file of files) {
      if (!file.name.endsWith(".yaml") && !file.name.endsWith(".yml")) continue;
      try {
        const yaml = await file.text();
        const planId = file.name.replace(/\.(yaml|yml)$/, "");
        // Avoid duplicates
        if (existingIds.has(planId)) {
          // Remove old version first
          const filtered = plans.filter(p => p.id !== planId);
          setPlans(filtered);
          existingIds.delete(planId);
        }
        await savePlan(planId, yaml, planId);
        imported++;
      } catch {
        // skip bad files
      }
    }
    // Refresh the list
    const refreshed = await listPlans();
    setPlans(refreshed);
    setBusy(false);
    setStatus(imported > 0 ? `Imported ${imported} plan(s).` : "No valid plan files found.");
  }, [plans, setPlans, sample, onChanged]);

  return (
    <StageShell
      n={3}
      group="Setup"
      title="Plans"
      description="Import commission plan YAML files. They become available to assign to payees in the next step."
      actions={plans.length > 0 && <Badge tone="accent">{plans.length} in library</Badge>}
      onBack={onBack}
      onNext={onNext}
    >
      {sample ? (
        <Callout tone="info">The sample plan is ready: each person earns 10% of their sales. Continue to check who is assigned to it.</Callout>
      ) : (
        <MultiUpload busy={busy} onFiles={handleFiles} />
      )}

      {status && <p role="status" className="text-[13px] text-ink-2">{status}</p>}

      {plans.length > 0 && (
        <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line">
          {plans.map(p => (
            <li key={p.id} className="flex items-center justify-between gap-3 bg-surface px-4 py-3">
              <div className="flex min-w-0 items-center gap-3">
                <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-accent-soft text-accent-ink">
                  <Icon name="fileText" />
                </span>
                <div className="min-w-0">
                  <p className="truncate text-[13.5px] font-medium text-ink">{p.name || p.id}</p>
                  <p className="truncate font-mono text-[12px] text-ink-3">{p.id}</p>
                </div>
              </div>
              <span className="shrink-0 text-xs text-ink-3">
                {p.updated_at ? `Updated ${new Date(p.updated_at).toLocaleDateString()}` : "Sample"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </StageShell>
  );
}

function MultiUpload({ busy, onFiles }: { busy: boolean; onFiles: (files: File[]) => void }) {
  return (
    <UploadZone
      label="Select plan files"
      title="Drop plan YAML files here"
      hint="One or more .yaml files. A plan with the same id replaces the saved one."
      accept=".yaml,.yml"
      icon="fileText"
      busy={busy}
      busyLabel="Importing plans..."
      multiple
      onFiles={onFiles}
    />
  );
}
