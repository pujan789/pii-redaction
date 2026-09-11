import { useEffect, useRef, useState } from "react";

export default function PdfPreview({
  title,
  blob,
  onClose,
}: {
  title: string;
  blob: Blob;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [url, setUrl] = useState<string>();
  useEffect(() => {
    const objectUrl = URL.createObjectURL(blob);
    setUrl(objectUrl);
    dialog.current?.showModal();
    return () => URL.revokeObjectURL(objectUrl);
  }, [blob]);
  return (
    <dialog
      ref={dialog}
      className="batch-preview"
      aria-labelledby="preview-title"
      onCancel={onClose}
    >
      <div className="batch-preview-heading">
        <div>
          <p className="eyebrow">Final redacted PDF (not editable)</p>
          <h2 id="preview-title">{title}</h2>
        </div>
        <button type="button" className="button button-secondary" onClick={onClose} autoFocus>
          Close preview
        </button>
      </div>
      {url && <iframe src={url} title={`Redacted preview of ${title}`} />}
    </dialog>
  );
}
