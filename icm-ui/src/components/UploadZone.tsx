import { useState } from "react";
import { Icon, Spinner, type IconName } from "./ui";
import { cx } from "./ui/cx";

/** A drop target that is also the file picker. The input carries the
 *  accessible name, so screen readers (and tests) find it by `label`.
 *  Pass `onFiles` (with `multiple`) to take several files at once. */
export default function UploadZone({
  label, title, hint, accept, busy, busyLabel = "Reading file...", icon = "upload", onFile, onFiles, multiple,
}: {
  label: string;
  title: string;
  hint: string;
  accept: string;
  busy?: boolean;
  busyLabel?: string;
  icon?: IconName;
  onFile?: (file: File) => void;
  onFiles?: (files: File[]) => void;
  multiple?: boolean;
}) {
  const [over, setOver] = useState(false);

  function take(list: FileList | null | undefined) {
    const files = Array.from(list ?? []);
    if (!files.length) return;
    if (onFiles) onFiles(files);
    else onFile?.(files[0]);
  }

  return (
    <label
      onDragOver={e => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={e => {
        e.preventDefault();
        setOver(false);
        if (!busy) take(e.dataTransfer.files);
      }}
      className={cx(
        "flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors",
        over ? "border-accent bg-accent-soft" : "border-line-strong hover:border-accent/50 hover:bg-surface-2/60",
        busy && "pointer-events-none opacity-70",
      )}
    >
      <span className="mb-1 grid h-11 w-11 place-items-center rounded-xl border border-line bg-surface text-accent-ink shadow-[var(--shadow-card)]">
        {busy ? <Spinner size={18} /> : <Icon name={icon} size={20} />}
      </span>
      <span className="text-sm font-semibold text-ink">{busy ? busyLabel : title}</span>
      <span className="max-w-md text-[13px] text-ink-2">{hint}</span>
      <span className="btn btn-secondary btn-sm mt-2">{multiple ? "Choose files" : "Choose file"}</span>
      <input
        aria-label={label}
        disabled={busy}
        type="file"
        accept={accept}
        multiple={multiple}
        className="hidden"
        onChange={e => {
          const files = e.target.files;
          take(files);
          e.target.value = "";
        }}
      />
    </label>
  );
}
