import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useConfigStore } from "@/stores/configStore";
import { useUiStore } from "@/stores/uiStore";
import { useEffect, useMemo, useState } from "react";
import { Save, RotateCcw, FolderOpen } from "lucide-react";
import * as tauri from "@/lib/tauri";

type EnvFieldType = "text" | "number" | "boolean";

interface EnvFieldDef {
  key: string;
  label: string;
  type: EnvFieldType;
  description?: string;
  placeholder?: string;
}

const GATEWAY_ENV_FIELDS: EnvFieldDef[] = [
  {
    key: "OPENCLAW_WEB_GATEWAY_ENABLED",
    label: "Web Gateway Enabled",
    type: "boolean",
  },
  {
    key: "OPENCLAW_WEB_GATEWAY_STRICT",
    label: "Strict Mode",
    type: "boolean",
    description: "Fail fast instead of best-effort behavior.",
  },
  {
    key: "OPENCLAW_WEB_GATEWAY_BASE_URL",
    label: "Base URL",
    type: "text",
    placeholder: "http://127.0.0.1:3003",
  },
  {
    key: "OPENCLAW_WEB_GATEWAY_REQUIRE_LOGIN",
    label: "Require Login",
    type: "boolean",
  },
  {
    key: "OPENCLAW_WEB_GATEWAY_TIMEOUT_SECONDS",
    label: "Timeout Seconds",
    type: "number",
    placeholder: "60",
  },
  {
    key: "OPENCLAW_WEB_GATEWAY_PREFLIGHT",
    label: "Preflight Checks",
    type: "boolean",
    description: "Verify session/login readiness before running jobs.",
  },
  {
    key: "OPENCLAW_WEB_GATEWAY_TRACE",
    label: "Trace Artifacts",
    type: "boolean",
    description: "Write screenshots/JSON traces to _VERIFY/.system/web_calls.",
  },
  {
    key: "OPENCLAW_WEB_GATEWAY_PROFILES_DIR",
    label: "Profiles Dir",
    type: "text",
    placeholder: "/absolute/path/to/profiles",
  },
  {
    key: "OPENCLAW_WEB_LLM_PRIMARY",
    label: "Primary Provider",
    type: "text",
    placeholder: "deepseek_web",
  },
  {
    key: "OPENCLAW_WEB_LLM_FALLBACK",
    label: "Fallback Provider",
    type: "text",
    placeholder: "chatgpt_web",
  },
  {
    key: "OPENCLAW_WEB_LLM_GENERATE_PRIMARY",
    label: "Generate Primary Provider",
    type: "text",
    placeholder: "chatgpt_web",
    description: "Optional override for translation/generation calls.",
  },
  {
    key: "OPENCLAW_WEB_LLM_GENERATE_FALLBACK",
    label: "Generate Fallback Provider",
    type: "text",
    placeholder: "deepseek_web",
    description: "Optional fallback override for translation/generation calls.",
  },
  {
    key: "OPENCLAW_WEB_LLM_REVIEW_PRIMARY",
    label: "Review Primary Provider",
    type: "text",
    placeholder: "deepseek_web",
    description: "Optional override for verification/review calls.",
  },
  {
    key: "OPENCLAW_WEB_LLM_REVIEW_FALLBACK",
    label: "Review Fallback Provider",
    type: "text",
    placeholder: "chatgpt_web",
    description: "Optional fallback override for verification/review calls.",
  },
  {
    key: "OPENCLAW_WEB_SESSION_MODE",
    label: "Session Mode",
    type: "text",
    placeholder: "per_job",
  },
];

function parseEnvBoolean(value: string | undefined): boolean {
  if (!value) return false;
  const normalized = value.trim().toLowerCase();
  return normalized === "1" || normalized === "true" || normalized === "yes" || normalized === "on";
}

function toEnvBoolean(value: boolean): string {
  return value ? "1" : "0";
}

export function Settings() {
  const config = useConfigStore((s) => s.config);
  const fetchConfig = useConfigStore((s) => s.fetchConfig);
  const saveConfig = useConfigStore((s) => s.saveConfig);
  const isLoading = useUiStore((s) => s.isLoading);
  const addToast = useUiStore((s) => s.addToast);

  const [localConfig, setLocalConfig] = useState({
    workRoot: "",
    kbRoot: "",
    strictRouter: false,
    requireNew: false,
    ragBackend: "local",
  });
  const [hasConfigChanges, setHasConfigChanges] = useState(false);

  const [envOriginal, setEnvOriginal] = useState<Record<string, string>>({});
  const [envDraft, setEnvDraft] = useState<Record<string, string>>({});
  const [isEnvLoading, setIsEnvLoading] = useState(false);
  const [isEnvSaving, setIsEnvSaving] = useState(false);

  useEffect(() => {
    void fetchConfig();
    void loadEnvSettings();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchConfig]);

  useEffect(() => {
    if (!config) return;
    setLocalConfig({
      workRoot: config.workRoot,
      kbRoot: config.kbRoot,
      strictRouter: config.strictRouter,
      requireNew: config.requireNew,
      ragBackend: config.ragBackend,
    });
    setHasConfigChanges(false);
  }, [config]);

  const loadEnvSettings = async () => {
    setIsEnvLoading(true);
    try {
      const entries = await tauri.getEnvSettings();
      const allowedKeys = new Set(GATEWAY_ENV_FIELDS.map((f) => f.key));
      const values: Record<string, string> = {};
      for (const entry of entries) {
        if (!allowedKeys.has(entry.key)) continue;
        values[entry.key] = entry.value;
      }
      for (const field of GATEWAY_ENV_FIELDS) {
        if (values[field.key] === undefined) values[field.key] = "";
      }
      setEnvOriginal(values);
      setEnvDraft(values);
    } catch (err) {
      addToast("error", `Failed to load env settings: ${err}`);
    } finally {
      setIsEnvLoading(false);
    }
  };

  const updateConfig = (key: keyof typeof localConfig, value: string | boolean) => {
    setLocalConfig((prev) => ({ ...prev, [key]: value }));
    setHasConfigChanges(true);
  };

  const handleBrowse = async (path: string) => {
    if (!path) return;
    try {
      await tauri.openInFinder(path);
    } catch (err) {
      console.error("Failed to open path:", err);
    }
  };

  const handleSaveConfig = async () => {
    try {
      await saveConfig({
        workRoot: localConfig.workRoot,
        kbRoot: localConfig.kbRoot,
        strictRouter: localConfig.strictRouter,
        requireNew: localConfig.requireNew,
        ragBackend: localConfig.ragBackend,
      });
      setHasConfigChanges(false);
    } catch {
      // keep unsaved state
    }
  };

  const handleResetConfig = () => {
    if (!config) return;
    setLocalConfig({
      workRoot: config.workRoot,
      kbRoot: config.kbRoot,
      strictRouter: config.strictRouter,
      requireNew: config.requireNew,
      ragBackend: config.ragBackend,
    });
    setHasConfigChanges(false);
  };

  const envChangedKeys = useMemo(() => {
    return GATEWAY_ENV_FIELDS.map((f) => f.key).filter((key) => (envDraft[key] ?? "") !== (envOriginal[key] ?? ""));
  }, [envDraft, envOriginal]);

  const hasEnvChanges = envChangedKeys.length > 0;

  const handleResetEnv = () => setEnvDraft(envOriginal);

  const handleSaveEnv = async () => {
    if (!hasEnvChanges) return;
    setIsEnvSaving(true);
    try {
      const updates = envChangedKeys.map((key) => ({ key, value: envDraft[key] ?? "" }));
      await tauri.saveEnvSettings(updates);
      await loadEnvSettings();
      addToast("success", `${updates.length} env settings saved`);
    } catch (err) {
      addToast("error", `Failed to save env settings: ${err}`);
    } finally {
      setIsEnvSaving(false);
    }
  };

  const handleOpenEnvFile = async () => {
    try {
      await tauri.openInFinder("/Users/Code/workflow/Inifity/.env.v4.local");
    } catch (err) {
      console.error("Failed to open .env.v4.local:", err);
      addToast("error", `Failed to open .env.v4.local: ${err}`);
    }
  };

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold">Settings</h2>
          <p className="text-muted-foreground">Paths, routing, RAG, and Web Gateway env</p>
        </div>
        <div className="flex items-center gap-2">
          {hasConfigChanges && <Badge variant="warning">Config changed</Badge>}
          {hasEnvChanges && <Badge variant="warning">Env changed</Badge>}
        </div>
      </div>

      <Card variant="glass">
        <CardHeader className="flex flex-row items-center justify-between gap-2">
          <CardTitle className="text-sm">Core Config</CardTitle>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={handleResetConfig} disabled={!hasConfigChanges || isLoading}>
              <RotateCcw className="h-4 w-4 mr-2" />
              Reset
            </Button>
            <Button size="sm" onClick={handleSaveConfig} disabled={!hasConfigChanges || isLoading}>
              <Save className="h-4 w-4 mr-2" />
              Save
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <div className="space-y-2">
              <label className="text-sm font-medium">Work Root</label>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={localConfig.workRoot}
                  onChange={(e) => updateConfig("workRoot", e.target.value)}
                  className="flex-1 px-3 py-2 border rounded-lg text-sm bg-background text-foreground"
                />
                <Button variant="outline" size="icon" onClick={() => void handleBrowse(localConfig.workRoot)}>
                  <FolderOpen className="h-4 w-4" />
                </Button>
              </div>
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium">KB Root</label>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={localConfig.kbRoot}
                  onChange={(e) => updateConfig("kbRoot", e.target.value)}
                  className="flex-1 px-3 py-2 border rounded-lg text-sm bg-background text-foreground"
                />
                <Button variant="outline" size="icon" onClick={() => void handleBrowse(localConfig.kbRoot)}>
                  <FolderOpen className="h-4 w-4" />
                </Button>
              </div>
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium">Routing</label>
              <div className="flex flex-col gap-2">
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={localConfig.strictRouter}
                    onChange={(e) => updateConfig("strictRouter", e.target.checked)}
                    className="rounded"
                  />
                  Strict Router
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={localConfig.requireNew}
                    onChange={(e) => updateConfig("requireNew", e.target.checked)}
                    className="rounded"
                  />
                  Require NEW before RUN
                </label>
              </div>
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium">RAG Backend</label>
              <select
                value={localConfig.ragBackend}
                onChange={(e) => updateConfig("ragBackend", e.target.value)}
                className="w-full px-3 py-2 border rounded-lg text-sm bg-background text-foreground"
              >
                <option value="clawrag">ClawRAG</option>
                <option value="local">Local</option>
                <option value="none">Disabled</option>
              </select>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card variant="glass">
        <CardHeader className="flex flex-row items-center justify-between gap-2">
          <CardTitle className="text-sm">Web Gateway Env</CardTitle>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={() => void loadEnvSettings()} disabled={isEnvLoading || isEnvSaving}>
              <RotateCcw className={`h-4 w-4 mr-2 ${isEnvLoading ? "animate-spin" : ""}`} />
              Reload
            </Button>
            <Button variant="outline" size="sm" onClick={handleResetEnv} disabled={!hasEnvChanges || isEnvSaving}>
              Reset
            </Button>
            <Button size="sm" onClick={handleSaveEnv} disabled={!hasEnvChanges || isEnvSaving}>
              <Save className="h-4 w-4 mr-2" />
              {isEnvSaving ? "Saving..." : "Save Env"}
            </Button>
            <Button variant="outline" size="sm" onClick={() => void handleOpenEnvFile()}>
              <FolderOpen className="h-4 w-4 mr-2" />
              Open File
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {isEnvLoading ? (
            <p className="text-sm text-muted-foreground">Loading env settings...</p>
          ) : (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
              {GATEWAY_ENV_FIELDS.map((field) => {
                const rawValue = envDraft[field.key] ?? "";
                const original = envOriginal[field.key] ?? "";
                const changed = rawValue !== original;

                return (
                  <div key={field.key} className="rounded-xl border border-border/50 bg-background/30 p-3 space-y-2">
                    <div className="flex items-center justify-between gap-2">
                      <label className="text-sm font-medium">{field.label}</label>
                      {changed && <Badge variant="warning">Changed</Badge>}
                    </div>
                    {field.description && <p className="text-[11px] text-muted-foreground">{field.description}</p>}

                    {field.type === "boolean" ? (
                      <select
                        value={parseEnvBoolean(rawValue) ? "1" : "0"}
                        onChange={(e) => setEnvDraft((prev) => ({ ...prev, [field.key]: toEnvBoolean(e.target.value === "1") }))}
                        className="w-full px-3 py-2 border rounded-lg text-sm bg-background text-foreground"
                      >
                        <option value="1">Enabled (1)</option>
                        <option value="0">Disabled (0)</option>
                      </select>
                    ) : (
                      <input
                        type={field.type === "number" ? "number" : "text"}
                        value={rawValue}
                        onChange={(e) => setEnvDraft((prev) => ({ ...prev, [field.key]: e.target.value }))}
                        placeholder={field.placeholder}
                        className="w-full px-3 py-2 border rounded-lg text-sm bg-background text-foreground"
                      />
                    )}

                    <p className="text-[11px] text-muted-foreground font-mono">{field.key}</p>
                  </div>
                );
              })}
            </div>
          )}

          {(hasConfigChanges || hasEnvChanges) && (
            <div className="rounded-xl border border-yellow-500/50 bg-yellow-500/5 p-3 text-sm text-muted-foreground">
              Some changes require restarting services to take effect.
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
