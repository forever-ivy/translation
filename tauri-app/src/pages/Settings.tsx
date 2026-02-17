import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useAppStore } from "@/stores/appStore";
import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Save, RotateCcw, FolderOpen, CheckCircle2, AlertCircle, Check, Sun, Moon, Monitor } from "lucide-react";
import * as tauri from "@/lib/tauri";

interface ConfigField {
  key: string;
  label: string;
  type: "text" | "path" | "number" | "boolean";
  value: string | number | boolean;
  description?: string;
  required?: boolean;
}

const configSections: { name: string; fields: ConfigField[] }[] = [
  {
    name: "Paths",
    fields: [
      {
        key: "workRoot",
        label: "Work Root",
        type: "path",
        value: "",
        required: true,
      },
      {
        key: "kbRoot",
        label: "Knowledge Base Root",
        type: "path",
        value: "",
        required: true,
      },
    ],
  },
  {
    name: "Routing",
    fields: [
      {
        key: "strictRouter",
        label: "Strict Router",
        type: "boolean",
        value: true,
        description: "Enforce new/run protocol",
      },
      {
        key: "requireNew",
        label: "Require New",
        type: "boolean",
        value: true,
        description: "Require 'new' before 'run'",
      },
    ],
  },
  {
    name: "RAG",
    fields: [
      {
        key: "ragBackend",
        label: "RAG Backend",
        type: "text",
        value: "clawrag",
      },
    ],
  },
];

const themeOptions = [
  { value: "light" as const, label: "Light", icon: Sun },
  { value: "dark" as const, label: "Dark", icon: Moon },
  { value: "system" as const, label: "System", icon: Monitor },
];

export function Settings() {
  const { config, isLoading, fetchConfig, saveConfig, theme, setTheme } = useAppStore();
  const [localConfig, setLocalConfig] = useState<Record<string, string | number | boolean>>({});
  const [hasChanges, setHasChanges] = useState(false);

  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);

  useEffect(() => {
    if (config) {
      setLocalConfig({
        workRoot: config.workRoot,
        kbRoot: config.kbRoot,
        strictRouter: config.strictRouter,
        requireNew: config.requireNew,
        ragBackend: config.ragBackend,
      });
    }
  }, [config]);

  const updateConfig = (key: string, value: string | number | boolean) => {
    setLocalConfig((prev) => ({ ...prev, [key]: value }));
    setHasChanges(true);
  };

  const handleSave = async () => {
    await saveConfig({
      workRoot: String(localConfig.workRoot || ""),
      kbRoot: String(localConfig.kbRoot || ""),
      strictRouter: Boolean(localConfig.strictRouter),
      requireNew: Boolean(localConfig.requireNew),
      ragBackend: String(localConfig.ragBackend || "local"),
    });
    setHasChanges(false);
  };

  const handleReset = () => {
    if (config) {
      setLocalConfig({
        workRoot: config.workRoot,
        kbRoot: config.kbRoot,
        strictRouter: config.strictRouter,
        requireNew: config.requireNew,
        ragBackend: config.ragBackend,
      });
      setHasChanges(false);
    }
  };

  const handleBrowse = async (key: string) => {
    const currentValue = localConfig[key];
    if (typeof currentValue === "string" && currentValue) {
      try {
        await tauri.openInFinder(currentValue);
      } catch (err) {
        console.error("Failed to open folder:", err);
      }
    }
  };

  const validateField = (key: string): "valid" | "invalid" | "unknown" => {
    const value = localConfig[key];
    if (value === undefined || value === "") return "unknown";
    if (key === "workRoot" || key === "kbRoot") {
      return String(value).startsWith("/") ? "valid" : "invalid";
    }
    return "valid";
  };

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold">Settings</h2>
          <p className="text-muted-foreground">Configure system parameters</p>
        </div>
        <div className="flex gap-2 items-center">
          <AnimatePresence>
            {hasChanges && (
              <motion.div
                initial={{ scale: 0.8, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                exit={{ scale: 0.8, opacity: 0 }}
              >
                <Badge variant="warning" className="flex items-center gap-1">
                  <AlertCircle className="h-3 w-3" />
                  Unsaved changes
                </Badge>
              </motion.div>
            )}
          </AnimatePresence>
          <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
            <Button variant="outline" onClick={handleReset} disabled={!hasChanges || isLoading}>
              <RotateCcw className="h-4 w-4 mr-2" />
              Reset
            </Button>
          </motion.div>
          <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
            <Button onClick={handleSave} disabled={!hasChanges || isLoading}>
              <Save className="h-4 w-4 mr-2" />
              Save Changes
            </Button>
          </motion.div>
        </div>
      </div>

      {/* Config Sections */}
      {configSections.map((section, sectionIndex) => (
        <motion.div
          key={section.name}
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: sectionIndex * 0.1 }}
        >
          <Card variant="glass">
            <CardHeader>
              <CardTitle className="text-sm">{section.name}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {section.fields.map((field, fieldIndex) => {
                const validation = validateField(field.key);

                return (
                  <motion.div
                    key={field.key}
                    initial={{ opacity: 0, x: -10 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: sectionIndex * 0.1 + fieldIndex * 0.05 }}
                    className="flex items-start gap-4"
                  >
                    <div className="flex-1">
                      <label className="block text-sm font-medium mb-1">
                        {field.label}
                        {field.required && <span className="text-red-500 ml-1">*</span>}
                      </label>
                      {field.description && (
                        <p className="text-xs text-muted-foreground mb-2">{field.description}</p>
                      )}
                      {field.type === "boolean" ? (
                        <label className="flex items-center gap-2 cursor-pointer">
                          <motion.input
                            type="checkbox"
                            checked={Boolean(localConfig[field.key])}
                            onChange={(e) => updateConfig(field.key, e.target.checked)}
                            className="rounded"
                            whileTap={{ scale: 0.9 }}
                          />
                          <span className="text-sm">Enabled</span>
                        </label>
                      ) : field.type === "path" ? (
                        <div className="flex gap-2">
                          <motion.input
                            type="text"
                            value={String(localConfig[field.key] || "")}
                            onChange={(e) => updateConfig(field.key, e.target.value)}
                            className="flex-1 px-3 py-2 border rounded-lg text-sm bg-background text-foreground focus:ring-2 focus:ring-primary focus:border-transparent transition-all"
                            whileFocus={{ scale: 1.01 }}
                          />
                          <motion.div whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.95 }}>
                            <Button variant="outline" size="icon" onClick={() => handleBrowse(field.key)}>
                              <FolderOpen className="h-4 w-4" />
                            </Button>
                          </motion.div>
                        </div>
                      ) : (
                        <motion.input
                          type={field.type === "number" ? "number" : "text"}
                          value={String(localConfig[field.key] || "")}
                          onChange={(e) =>
                            updateConfig(field.key, field.type === "number" ? Number(e.target.value) : e.target.value)
                          }
                          className="w-full px-3 py-2 border rounded-lg text-sm bg-background text-foreground focus:ring-2 focus:ring-primary focus:border-transparent transition-all"
                          whileFocus={{ scale: 1.01 }}
                        />
                      )}
                    </div>
                    <div className="pt-6">
                      <AnimatePresence mode="wait">
                        {validation === "valid" && (
                          <motion.div
                            key="valid"
                            initial={{ scale: 0 }}
                            animate={{ scale: 1 }}
                            exit={{ scale: 0 }}
                            transition={{ type: "spring", stiffness: 500 }}
                          >
                            <CheckCircle2 className="h-4 w-4 text-green-500" />
                          </motion.div>
                        )}
                        {validation === "invalid" && (
                          <motion.div
                            key="invalid"
                            initial={{ scale: 0 }}
                            animate={{ scale: 1 }}
                            exit={{ scale: 0 }}
                            transition={{ type: "spring", stiffness: 500 }}
                          >
                            <AlertCircle className="h-4 w-4 text-red-500" />
                          </motion.div>
                        )}
                      </AnimatePresence>
                    </div>
                  </motion.div>
                );
              })}
            </CardContent>
          </Card>
        </motion.div>
      ))}

      {/* Appearance Section */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: configSections.length * 0.1 }}
      >
        <Card variant="glass">
          <CardHeader>
            <CardTitle className="text-sm">Appearance</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium mb-2">Theme</label>
                <p className="text-xs text-muted-foreground mb-3">Choose your preferred color scheme</p>
                <div className="flex gap-2">
                  {themeOptions.map((option) => {
                    const Icon = option.icon;
                    const isActive = theme === option.value;
                    return (
                      <motion.button
                        key={option.value}
                        onClick={() => setTheme(option.value)}
                        whileHover={{ scale: 1.02 }}
                        whileTap={{ scale: 0.98 }}
                        className={`
                          flex items-center gap-2 px-4 py-2 rounded-full
                          transition-all duration-200
                          ${isActive
                            ? "bg-primary text-primary-foreground shadow-md"
                            : "bg-muted/50 hover:bg-muted text-muted-foreground hover:text-foreground"
                          }
                        `}
                      >
                        <Icon className="h-4 w-4" />
                        <span className="text-sm font-medium">{option.label}</span>
                        {isActive && (
                          <motion.div
                            initial={{ scale: 0 }}
                            animate={{ scale: 1 }}
                            transition={{ type: "spring", stiffness: 500 }}
                          >
                            <Check className="h-3 w-3" />
                          </motion.div>
                        )}
                      </motion.button>
                    );
                  })}
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      </motion.div>

      {/* Effect Notice */}
      <AnimatePresence>
        {hasChanges && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 20 }}
          >
            <Card className="border-yellow-500/50 bg-yellow-500/5">
              <CardContent className="flex items-center gap-3 p-4">
                <motion.div
                  animate={{ scale: [1, 1.1, 1] }}
                  transition={{ duration: 2, repeat: Infinity }}
                >
                  <AlertCircle className="h-5 w-5 text-yellow-500" />
                </motion.div>
                <div>
                  <p className="font-medium text-sm">Some changes require service restart</p>
                  <p className="text-xs text-muted-foreground">
                    RAG and routing settings will take effect after restarting services
                  </p>
                </div>
              </CardContent>
            </Card>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Advanced Note */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.3 }}
      >
        <Card variant="glass">
          <CardHeader>
            <CardTitle className="text-sm">Advanced Configuration</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground mb-2">
              Additional settings can be configured by editing the .env.v4.local file directly.
            </p>
            <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
              <Button
                variant="outline"
                size="sm"
                onClick={async () => {
                  await tauri.openInFinder("/Users/Code/workflow/translation/.env.v4.local");
                }}
              >
                <FolderOpen className="h-4 w-4 mr-2" />
                Open Config File
              </Button>
            </motion.div>
          </CardContent>
        </Card>
      </motion.div>
    </div>
  );
}
