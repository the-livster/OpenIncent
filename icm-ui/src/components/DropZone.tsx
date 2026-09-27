import { useCallback, useRef, useState } from "react";
import { Button, Icon, type IconName } from "./ui";
import { cx } from "./ui/cx";

interface Props {
  file: File | null;
  onChange: (f: File | null) => void;
  accept: string;
  label: string;
  icon?: IconName;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function DropZone({ file, onChange, accept, label, icon }: Props) {
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const f = e.dataTransfer.files[0];
      if (f) onChange(f);
    },
    [onChange],
  );

  const glyph: IconName = icon || (accept.includes("yaml") ? "fileText" : "table");

  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
      onClick={() => inputRef.current?.click()}
      className={cx(
        "flex cursor-pointer items-center gap-3 rounded-xl border-2 border-dashed p-4 transition-colors",
        dragOver ? "border-accent bg-accent-soft"
          : file ? "border-accent/40 bg-accent-soft/60"
          : "border-line-strong hover:border-accent/50 hover:bg-surface-2/60",
      )}
    >
      <input
        ref={inputRef}
        type="file"
        aria-label={label}
        accept={accept}
        className="hidden"
        onChange={(e) => onChange(e.target.files?.[0] ?? null)}
      />
      <span className={cx(
        "grid h-10 w-10 shrink-0 place-items-center rounded-lg border",
        file ? "border-accent/30 bg-surface text-accent-ink" : "border-line bg-surface text-ink-3",
      )}>
        <Icon name={file ? "checkCircle" : glyph} size={18} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-[13.5px] font-medium text-ink">{label}</div>
        {file ? (
          <div className="mt-0.5 flex items-center gap-2 text-[12.5px]">
            <span className="truncate font-mono text-ink">{file.name}</span>
            <span className="shrink-0 text-ink-3">{formatSize(file.size)}</span>
          </div>
        ) : (
          <div className="mt-0.5 text-[12.5px] text-ink-2">Drag and drop, or click to browse</div>
        )}
      </div>
      {file && (
        <Button
          size="sm"
          variant="ghost"
          icon="x"
          aria-label="Remove file"
          onClick={(e) => { e.stopPropagation(); onChange(null); }}
        />
      )}
    </div>
  );
}
