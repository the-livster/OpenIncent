import { useCallback, useRef, useState } from "react";

interface Props {
  label: string;
  accept: string;
  file: File | null;
  onChange: (f: File | null) => void;
  icon: string;
}

export default function FileUpload({ label, accept, file, onChange, icon }: Props) {
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const f = e.dataTransfer.files[0];
      if (f) onChange(f);
    },
    [onChange]
  );

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
      onClick={() => inputRef.current?.click()}
      className={`
        group relative rounded-xl border-2 border-dashed p-5
        transition-all duration-200 cursor-pointer
        ${dragOver
          ? "border-brand-400 bg-brand-500/10 scale-[1.02]"
          : file
            ? "border-brand-500/40 bg-brand-500/5"
            : "border-surface-300 bg-surface-100/50 hover:border-surface-400 hover:bg-surface-100"
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

      <div className="flex items-center gap-3">
        <div className={`
          text-2xl w-10 h-10 rounded-lg flex items-center justify-center
          transition-colors duration-200
          ${file ? "bg-brand-500/20 text-brand-400" : "bg-surface-200 text-surface-500 group-hover:text-surface-600"}
        `}>
          {icon}
        </div>

        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-surface-700">{label}</div>
          {file ? (
            <div className="flex items-center gap-2 mt-0.5">
              <span className="text-xs text-surface-900 font-mono truncate">{file.name}</span>
              <span className="text-xs text-surface-500 shrink-0">{formatSize(file.size)}</span>
            </div>
          ) : (
            <div className="text-xs text-surface-500 mt-0.5">
              Drag & drop or click to browse
            </div>
          )}
        </div>

        {file && (
          <button
            onClick={(e) => { e.stopPropagation(); onChange(null); }}
            className="text-surface-500 hover:text-danger text-sm transition-colors p-1"
            title="Remove file"
          >
            ✕
          </button>
        )}
      </div>
    </div>
  );
}
