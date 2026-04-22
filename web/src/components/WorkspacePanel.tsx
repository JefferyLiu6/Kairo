import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  fetchWorkspace,
  fetchWorkspaceFile,
  runWorkspaceFile,
  saveWorkspaceFile,
  createWorkspaceDir,
  deleteWorkspaceEntry,
  type WorkspaceFile,
  type RunResult,
} from "../api";
import { InteractiveTerminal } from "./InteractiveTerminal";

const RUNNABLE: Record<string, string> = {
  py: "Python", js: "Node", ts: "tsx", sh: "Bash", go: "Go", rb: "Ruby",
};

const PREVIEWABLE_HTML = new Set(["html", "htm"]);
const PREVIEWABLE_MD = new Set(["md", "markdown"]);

function fileExt(name: string) { return name.split(".").pop()?.toLowerCase() ?? ""; }
function formatMs(ms: number) { return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`; }

type Props = { onClose: () => void };
type NewItemState = { parentPath: string | null; type: "file" | "dir" };

function FileTree({
  nodes, onSelect, selected, onDelete, onNew, confirmDelete, setConfirmDelete,
}: {
  nodes: WorkspaceFile[];
  onSelect: (f: WorkspaceFile) => void;
  selected: string | null;
  onDelete: (path: string) => void;
  onNew: (parentPath: string, type: "file") => void;
  confirmDelete: string | null;
  setConfirmDelete: (p: string | null) => void;
}) {
  const [open, setOpen] = useState<Set<string>>(new Set());

  function toggle(path: string) {
    setOpen(p => { const n = new Set(p); n.has(path) ? n.delete(path) : n.add(path); return n; });
  }

  return (
    <ul className="ws-tree">
      {nodes.map(node => (
        <li key={node.path}>
          {node.type === "dir" ? (
            <>
              <div className="ws-tree-row">
                <button className="ws-tree-dir" onClick={() => toggle(node.path)}>
                  <span className="ws-tree-caret">{open.has(node.path) ? "▾" : "▸"}</span>
                  <span className="ws-tree-icon">⊡</span>
                  <span className="ws-tree-name">{node.name}</span>
                </button>
                <span className="ws-tree-actions">
                  <button
                    className="ws-tree-act"
                    title="New file inside"
                    onClick={e => { e.stopPropagation(); onNew(node.path, "file"); }}
                  >+</button>
                  <button
                    className="ws-tree-act ws-tree-act-del"
                    title="Delete folder"
                    onClick={e => { e.stopPropagation(); setConfirmDelete(node.path); }}
                  >×</button>
                </span>
              </div>
              {confirmDelete === node.path && (
                <div className="ws-delete-confirm">
                  <span>Delete "{node.name}"?</span>
                  <button className="ws-del-yes" onClick={() => onDelete(node.path)}>Delete</button>
                  <button className="ws-del-no" onClick={() => setConfirmDelete(null)}>Cancel</button>
                </div>
              )}
              {open.has(node.path) && node.children && (
                <FileTree
                  nodes={node.children}
                  onSelect={onSelect}
                  selected={selected}
                  onDelete={onDelete}
                  onNew={onNew}
                  confirmDelete={confirmDelete}
                  setConfirmDelete={setConfirmDelete}
                />
              )}
            </>
          ) : (
            <>
              <div className="ws-tree-row">
                <button
                  className={`ws-tree-file ${selected === node.path ? "active" : ""}`}
                  onClick={() => onSelect(node)}
                >
                  <span className="ws-tree-icon">⊞</span>
                  <span className="ws-tree-name">{node.name}</span>
                  {node.size !== undefined && (
                    <span className="ws-tree-size">
                      {node.size < 1024 ? `${node.size}B` : `${(node.size / 1024).toFixed(1)}K`}
                    </span>
                  )}
                </button>
                <span className="ws-tree-actions">
                  <button
                    className="ws-tree-act ws-tree-act-del"
                    title="Delete file"
                    onClick={e => { e.stopPropagation(); setConfirmDelete(node.path); }}
                  >×</button>
                </span>
              </div>
              {confirmDelete === node.path && (
                <div className="ws-delete-confirm">
                  <span>Delete "{node.name}"?</span>
                  <button className="ws-del-yes" onClick={() => onDelete(node.path)}>Delete</button>
                  <button className="ws-del-no" onClick={() => setConfirmDelete(null)}>Cancel</button>
                </div>
              )}
            </>
          )}
        </li>
      ))}
    </ul>
  );
}

function Terminal({ result, running }: { result: RunResult | null; running: boolean }) {
  if (!running && !result) return null;
  return (
    <div className="ws-terminal">
      <div className="ws-terminal-bar">
        <span className="ws-terminal-label">Terminal</span>
        {result && (
          <span className={`ws-terminal-exit ${result.timedOut ? "timeout" : result.exitCode === 0 ? "ok" : "err"}`}>
            {result.timedOut ? "⏱ timed out" : result.exitCode === 0 ? "✓ exit 0" : `✕ exit ${result.exitCode}`}
          </span>
        )}
        {result && <span className="ws-terminal-meta">{result.image} · {formatMs(result.durationMs)}</span>}
      </div>
      <div className="ws-terminal-body">
        {running && <span className="ws-terminal-running"><span className="ws-terminal-spinner" /> Running…</span>}
        {result?.stdout && <pre className="ws-terminal-stdout">{result.stdout}</pre>}
        {result?.stderr && <pre className="ws-terminal-stderr">{result.stderr}</pre>}
      </div>
    </div>
  );
}

export function WorkspacePanel({ onClose }: Props) {
  const [files, setFiles] = useState<WorkspaceFile[]>([]);
  const [root, setRoot] = useState<string | null>(null);
  const [selected, setSelected] = useState<WorkspaceFile | null>(null);
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [fileLoading, setFileLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<RunResult | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [interactive, setInteractive] = useState(false);
  const [preview, setPreview] = useState(false);

  // Edit mode
  const [editing, setEditing] = useState(false);
  const [editContent, setEditContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);

  // New item creation
  const [newItem, setNewItem] = useState<NewItemState | null>(null);
  const [newItemName, setNewItemName] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const newItemInputRef = useRef<HTMLInputElement>(null);

  // Delete confirmation
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const runAbortRef = useRef<AbortController | null>(null);

  async function refreshTree() {
    const { root: r, files: f } = await fetchWorkspace();
    setRoot(r);
    setFiles(f);
  }

  useEffect(() => {
    fetchWorkspace().then(({ root: r, files: f }) => {
      setRoot(r);
      setFiles(f);
      setLoading(false);
    });
  }, []);

  useEffect(() => {
    if (newItem) {
      setTimeout(() => newItemInputRef.current?.focus(), 50);
    }
  }, [newItem]);

  async function handleSelect(file: WorkspaceFile) {
    setSelected(file);
    setContent(null);
    setRunResult(null);
    setRunError(null);
    setInteractive(false);
    setPreview(false);
    setEditing(false);
    setSaveError(null);
    setSaveMsg(null);
    setFileLoading(true);
    const text = await fetchWorkspaceFile(file.path);
    setContent(text ?? "Could not read file.");
    setFileLoading(false);
  }

  async function handleRun() {
    if (!selected) return;
    const runLang = RUNNABLE[fileExt(selected.name)];
    runAbortRef.current?.abort();
    const ctrl = new AbortController();
    runAbortRef.current = ctrl;
    setRunning(true);
    setRunResult(null);
    setRunError(null);
    try {
      const result = await runWorkspaceFile(selected.path, runLang, ctrl.signal);
      setRunResult(result);
    } catch (err) {
      if ((err as Error).name === "AbortError") {
        setRunError("Stopped.");
      } else {
        setRunError(err instanceof Error ? err.message : "Unknown error");
      }
    } finally {
      setRunning(false);
      runAbortRef.current = null;
    }
  }

  function handleStopRun() {
    runAbortRef.current?.abort();
  }

  function handleEditStart() {
    setEditing(true);
    setEditContent(content ?? "");
    setSaveError(null);
    setSaveMsg(null);
    setRunResult(null);
    setRunError(null);
    setInteractive(false);
  }

  function handleEditCancel() {
    setEditing(false);
    setSaveError(null);
  }

  async function handleSave() {
    if (!selected) return;
    setSaving(true);
    setSaveError(null);
    try {
      const ok = await saveWorkspaceFile(selected.path, editContent);
      if (!ok) throw new Error("Save failed");
      setContent(editContent);
      setEditing(false);
      setSaveMsg("Saved");
      setTimeout(() => setSaveMsg(null), 2000);
      await refreshTree();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  function triggerNew(parentPath: string | null, type: "file" | "dir") {
    setNewItem({ parentPath, type });
    setNewItemName("");
    setCreateError(null);
  }

  async function handleCreateSubmit() {
    if (!newItemName.trim() || !newItem) return;
    setCreating(true);
    setCreateError(null);
    try {
      const base = newItem.parentPath ? `${newItem.parentPath}/` : "";
      const fullPath = `${base}${newItemName.trim()}`;
      if (newItem.type === "dir") {
        const ok = await createWorkspaceDir(fullPath);
        if (!ok) throw new Error("Failed to create folder");
      } else {
        const ok = await saveWorkspaceFile(fullPath, "");
        if (!ok) throw new Error("Failed to create file");
      }
      setNewItem(null);
      setNewItemName("");
      await refreshTree();
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "Create failed");
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(path: string) {
    setConfirmDelete(null);
    const ok = await deleteWorkspaceEntry(path);
    if (ok) {
      if (selected?.path === path || selected?.path.startsWith(path + "/")) {
        setSelected(null);
        setContent(null);
        setEditing(false);
      }
      await refreshTree();
    }
  }

  const rootLabel = root ? root.split("/").slice(-2).join("/") : null;
  const ext = selected ? fileExt(selected.name) : "";
  const lang = RUNNABLE[ext];
  const isHtml = PREVIEWABLE_HTML.has(ext);
  const isMd = PREVIEWABLE_MD.has(ext);

  return (
    <div className="ws-overlay" onClick={onClose}>
      <div className="ws-panel" onClick={e => e.stopPropagation()}>

        <div className="ws-header">
          <div className="ws-header-left">
            <span className="ws-title">Workspace</span>
            {rootLabel && <span className="ws-root">{rootLabel}</span>}
          </div>
          <button className="history-close" onClick={onClose}>✕</button>
        </div>

        <div className="ws-body">
          {/* Sidebar */}
          <div className="ws-sidebar">
            <div className="ws-sidebar-toolbar">
              <button className="ws-toolbar-btn" onClick={() => triggerNew(null, "file")} title="New file at root">
                + File
              </button>
              <button className="ws-toolbar-btn" onClick={() => triggerNew(null, "dir")} title="New folder at root">
                + Folder
              </button>
            </div>

            {newItem && (
              <div className="ws-new-item-form">
                <div className="ws-new-type-toggle">
                  <button
                    className={`ws-new-type-btn ${newItem.type === "file" ? "active" : ""}`}
                    onClick={() => setNewItem(prev => prev ? { ...prev, type: "file" } : null)}
                    title="File"
                  >📄</button>
                  <button
                    className={`ws-new-type-btn ${newItem.type === "dir" ? "active" : ""}`}
                    onClick={() => setNewItem(prev => prev ? { ...prev, type: "dir" } : null)}
                    title="Folder"
                  >📁</button>
                </div>
                <input
                  ref={newItemInputRef}
                  className="ws-new-item-input"
                  value={newItemName}
                  onChange={e => setNewItemName(e.target.value)}
                  placeholder={
                    newItem.parentPath
                      ? `${newItem.parentPath}/…`
                      : newItem.type === "dir" ? "folder-name" : "file.txt"
                  }
                  onKeyDown={e => {
                    if (e.key === "Enter") handleCreateSubmit();
                    if (e.key === "Escape") { setNewItem(null); setCreateError(null); }
                  }}
                />
                <button className="ws-new-item-submit" onClick={handleCreateSubmit} disabled={creating}>
                  {creating ? "…" : "✓"}
                </button>
                <button className="ws-new-item-cancel" onClick={() => { setNewItem(null); setCreateError(null); }}>×</button>
              </div>
            )}

            {createError && <div className="ws-create-error">{createError}</div>}

            <div className="ws-tree-scroll">
              {loading ? (
                <div className="ws-empty">Loading…</div>
              ) : files.length === 0 ? (
                <div className="ws-empty">
                  {root ? "Workspace is empty." : "WORKSPACE_DIR not configured."}
                </div>
              ) : (
                <FileTree
                  nodes={files}
                  onSelect={handleSelect}
                  selected={selected?.path ?? null}
                  onDelete={handleDelete}
                  onNew={triggerNew}
                  confirmDelete={confirmDelete}
                  setConfirmDelete={setConfirmDelete}
                />
              )}
            </div>
          </div>

          {/* Content area */}
          <div className="ws-content">
            {!selected ? (
              <div className="ws-empty ws-content-empty">Select a file to view</div>
            ) : fileLoading ? (
              <div className="ws-empty">Loading…</div>
            ) : (
              <>
                <div className="ws-file-header">
                  <span className="ws-file-path">{selected.path}</span>

                  {(isHtml || isMd) && !editing && (
                    <div className="ws-preview-tabs">
                      <button className={`ws-preview-tab ${!preview ? "active" : ""}`} onClick={() => setPreview(false)}>Source</button>
                      <button className={`ws-preview-tab ${preview ? "active" : ""}`} onClick={() => setPreview(true)}>Preview</button>
                    </div>
                  )}

                  {!editing ? (
                    <>
                      <button className="ws-edit-btn" onClick={handleEditStart} title="Edit this file">
                        ✎ Edit
                      </button>
                      {lang && !interactive && (
                        <>
                          <button
                            className="ws-run-btn"
                            onClick={handleRun}
                            disabled={running}
                            title={`Run with ${lang} (non-interactive)`}
                          >
                            {running ? <><span className="ws-run-spinner" /> Running…</> : <>▶ Run</>}
                          </button>
                          {running && (
                            <button
                              type="button"
                              className="ws-run-btn ws-run-btn-stop"
                              onClick={handleStopRun}
                              title="Abort this run"
                            >
                              Stop
                            </button>
                          )}
                          <button
                            className="ws-run-btn ws-run-btn-interactive"
                            onClick={() => { setRunResult(null); setRunError(null); setInteractive(true); }}
                            title="Run interactively in a live terminal"
                          >
                            ⌨ Interactive
                          </button>
                        </>
                      )}
                      {interactive && (
                        <button className="ws-run-btn ws-run-btn-stop" onClick={() => setInteractive(false)}>
                          ✕ Close terminal
                        </button>
                      )}
                      {saveMsg && <span className="ws-save-flash">{saveMsg}</span>}
                    </>
                  ) : (
                    <>
                      <button className="ws-save-btn" onClick={handleSave} disabled={saving}>
                        {saving ? "Saving…" : "✓ Save"}
                      </button>
                      <button className="ws-edit-btn" onClick={handleEditCancel}>✕ Cancel</button>
                    </>
                  )}
                </div>

                {saveError && <div className="ws-save-error">✕ {saveError}</div>}
                {runError && <div className="ws-run-error">✕ {runError}</div>}

                {editing ? (
                  <textarea
                    className="ws-file-edit"
                    value={editContent}
                    onChange={e => setEditContent(e.target.value)}
                    spellCheck={false}
                  />
                ) : preview && isHtml ? (
                  <iframe
                    className="ws-html-preview"
                    src={`/workspace/raw/${selected.path}`}
                    sandbox="allow-scripts allow-same-origin"
                    title="HTML Preview"
                  />
                ) : preview && isMd ? (
                  <div className="ws-md-preview">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {content ?? ""}
                    </ReactMarkdown>
                  </div>
                ) : (
                  <pre className="ws-file-content"><code>{content}</code></pre>
                )}

                {!editing && !interactive && <Terminal result={runResult} running={running} />}
                {!editing && interactive && (
                  <InteractiveTerminal
                    key={selected.path}
                    path={selected.path}
                    onExit={() => setInteractive(false)}
                  />
                )}
              </>
            )}
          </div>
        </div>

      </div>
    </div>
  );
}
