import { useCallback, useState } from "react";
import { listPlans, savePlan } from "../api";
import type { SavedPlan } from "../types";

interface Props {
  plans: SavedPlan[];
  setPlans: (p: SavedPlan[]) => void;
  onNext: () => void;
  onBack: () => void;
}

export default function StagePlans({ plans, setPlans, onNext, onBack }: Props) {
  const [dragOver, setDragOver] = useState(false);
  const [status, setStatus] = useState("");

  const handleFiles = useCallback(async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setStatus(`Importing ${files.length} plan(s)...`);
    const existingIds = new Set(plans.map(p => p.id));
    let imported = 0;
    for (const file of Array.from(files)) {
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
    setStatus(imported > 0 ? `Imported ${imported} plan(s).` : "No valid plan files found.");
  }, [plans, setPlans]);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    handleFiles(e.dataTransfer.files);
  }, [handleFiles]);

  return (
    <div className="max-w-3xl space-y-4">
      <h1 className="text-lg font-bold text-zinc-800">2b. Plans</h1>
      <p className="text-sm text-zinc-500">
        Import commission plan YAML files. These will be available to assign to payees in the next step.
      </p>

      <div
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        className={`border-2 border-dashed rounded-xl p-8 text-center space-y-3 transition-all cursor-pointer
          ${dragOver ? "border-blue-400 bg-blue-50" : "border-zinc-300 hover:border-zinc-400"}`}
      >
        <label className="inline-block px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 cursor-pointer">
          Select Plan Files
          <input
            type="file"
            accept=".yaml,.yml"
            multiple
            className="hidden"
            onChange={e => handleFiles(e.target.files)}
          />
        </label>
        <p className="text-xs text-zinc-400">or drag and drop .yaml files here</p>
      </div>

      {status && (
        <div className="text-sm text-zinc-600 bg-zinc-50 rounded-lg px-3 py-2">{status}</div>
      )}

      {plans.length > 0 && (
        <div className="space-y-2">
          <div className="text-xs font-medium text-zinc-500">
            {plans.length} plan(s) in library
          </div>
          <div className="max-h-48 overflow-y-auto space-y-1">
            {plans.map(p => (
              <div key={p.id} className="flex items-center justify-between px-3 py-2 rounded-lg bg-zinc-50 text-sm">
                <div>
                  <span className="font-medium text-zinc-700">{p.id}</span>
                  {p.name && <span className="text-zinc-500 ml-2">— {p.name}</span>}
                </div>
                <span className="text-xs text-zinc-400">
                  {new Date(p.updated_at).toLocaleDateString()}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="flex justify-between pt-2">
        <button onClick={onBack} className="text-sm text-zinc-500 hover:text-zinc-700 cursor-pointer">
          ← Back
        </button>
        <button onClick={onNext} className="px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 cursor-pointer">
          Continue →
        </button>
      </div>
    </div>
  );
}
