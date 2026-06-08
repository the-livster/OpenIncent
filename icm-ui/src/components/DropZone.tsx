import { useCallback, useRef, useState } from "react";

interface Props {
  file: File | null;
  onChange: (f: File | null) => void;
  accept: string;
  label: string;
  icon?: string;
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

  const emoji = icon || (accept.includes("csv") ? "📊" : accept.includes("yaml") ? "📋" : "📄");

  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
      className={`
        rounded-xl border-2 border-dashed p-5 cursor-pointer transition-all
        ${dragOver
          ? "border-accent bg-accent/10 scale-[1.01]"
          : file
            ? "border-accent bg-accent/5"
            : "border-line bg-soft hover:border-ink2"
        }
      `}
    >
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        className="hidden"
        onChange={(e) => onChange(e.target.files?.[0] ?? null)}
      />

      <div
        onClick={() => inputRef.current?.click()}
        className="flex items-center gap-3 cursor-pointer"
      >
        <div className={`
          text-2xl w-10 h-10 rounded-lg flex items-center justify-center shrink-0
          ${file ? "bg-accent/20 text-accent" : "bg-soft text-ink2"}
        `}>
          {emoji}
        </div>

        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-ink">{label}</div>
          {file ? (
            <div className="flex items-center gap-2 mt-0.5">
              <span className="text-xs text-ink font-mono truncate">{file.name}</span>
              <span className="text-xs text-ink2 shrink-0">{formatSize(file.size)}</span>
            </div>
          ) : (
            <div className="text-xs text-ink2 mt-0.5">
              Drag & drop or click to browse
            </div>
          )}
        </div>

        {file && (
          <button
            onClick={(e) => { e.stopPropagation(); onChange(null); }}
            className="text-ink2 hover:text-danger text-sm transition-colors p-1 shrink-0 cursor-pointer"
            title="Remove file"
          >
            ✕
          </button>
        )}
      </div>
    </div>
  );
}
