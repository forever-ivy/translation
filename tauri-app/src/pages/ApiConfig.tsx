import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useAppStore, ApiProvider, ApiUsage, ModelAvailabilityReport } from "@/stores/appStore";
import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Key,
  RefreshCw,
  Eye,
  EyeOff,
  Trash2,
  CheckCircle2,
  AlertCircle,
  Clock,
  Zap,
} from "lucide-react";

const USAGE_REFRESH_INTERVAL = 60000; // 1 minute

function getStatusBadge(status: ApiProvider["status"]) {
  switch (status) {
    case "configured":
      return <Badge variant="success">Configured</Badge>;
    case "missing":
      return <Badge variant="secondary">Not Configured</Badge>;
    case "expired":
      return <Badge variant="warning">Expired</Badge>;
    default:
      return <Badge variant="outline">Unknown</Badge>;
  }
}

function getAuthTypeLabel(authType: ApiProvider["authType"]) {
  switch (authType) {
    case "oauth":
      return "OAuth";
    case "api_key":
      return "API Key";
    case "none":
      return "None";
    default:
      return authType;
  }
}

function getRouteStateBadge(state: string) {
  switch (state) {
    case "ok":
      return <Badge variant="success">OK</Badge>;
    case "cooldown":
      return <Badge variant="warning">Cooldown</Badge>;
    case "unavailable":
      return <Badge variant="destructive">Unavailable</Badge>;
    case "expired":
      return <Badge variant="warning">Expired</Badge>;
    case "unknown":
    default:
      return <Badge variant="outline">Unknown</Badge>;
  }
}

function AgentAvailabilityCard({
  title,
  agent,
}: {
  title: string;
  agent: ModelAvailabilityReport["agents"][string] | undefined;
}) {
  if (!agent) {
    return (
      <Card variant="glass">
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <Zap className="h-4 w-4 text-primary" />
            {title}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-sm text-muted-foreground">
            Availability data unavailable.
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card variant="glass">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between gap-3">
          <CardTitle className="text-base flex items-center gap-2">
            <Zap className="h-4 w-4 text-primary" />
            {title}
          </CardTitle>
          <Badge variant={agent.runnable_now ? "success" : "destructive"}>
            {agent.runnable_now ? "Runnable" : "Blocked"}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="text-xs text-muted-foreground">
          Default:{" "}
          <code className="text-xs bg-muted px-1 rounded">
            {agent.default_model}
          </code>
        </div>

        <div className="space-y-2">
          {agent.route.map((r) => (
            <div
              key={r.model}
              className="flex items-start justify-between gap-3 p-2 rounded-xl border bg-background/40"
            >
              <div className="min-w-0">
                <div className="text-xs font-mono break-all">{r.model}</div>
                <div className="text-[10px] text-muted-foreground">
                  Provider: {r.provider}
                  {typeof r.available === "boolean" && (
                    <> • Available: {r.available ? "yes" : "no"}</>
                  )}
                </div>
                {r.cooldown_until_ms && (
                  <div className="text-[10px] text-muted-foreground">
                    Cooldown until: {new Date(r.cooldown_until_ms).toLocaleString()}
                  </div>
                )}
                {r.note && (
                  <div className="text-[10px] text-muted-foreground">
                    Note: {r.note}
                  </div>
                )}
              </div>
              <div className="shrink-0">{getRouteStateBadge(r.state)}</div>
            </div>
          ))}
        </div>

        {!agent.runnable_now && agent.blocked_reasons.length > 0 && (
          <div className="rounded-xl border bg-red-500/5 border-red-500/20 p-3">
            <div className="text-sm font-medium mb-2">Blocked reasons</div>
            <ul className="text-xs text-muted-foreground list-disc pl-4 space-y-1">
              {agent.blocked_reasons.map((reason, idx) => (
                <li key={idx}>{reason}</li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function UsageBar({ usage }: { usage: ApiUsage | undefined }) {
  if (!usage || usage.limit === 0) {
    return (
      <div className="text-xs text-muted-foreground">
        Usage data unavailable
      </div>
    );
  }

  const percentage = usage.limit > 0 ? (usage.remaining / usage.limit) * 100 : 0;
  const usedPercentage = 100 - percentage;

  let barColor = "bg-green-500";
  if (percentage < 20) {
    barColor = "bg-red-500";
  } else if (percentage < 50) {
    barColor = "bg-yellow-500";
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-xs">
        <span className="text-muted-foreground">
          {usage.used.toLocaleString()} / {usage.limit.toLocaleString()} {usage.unit}
        </span>
        <span className="font-medium">
          {usage.remaining.toLocaleString()} remaining
        </span>
      </div>
      <div className="h-2 bg-muted rounded-full overflow-hidden">
        <motion.div
          className={`h-full ${barColor}`}
          initial={{ width: 0 }}
          animate={{ width: `${usedPercentage}%` }}
          transition={{ duration: 0.5, ease: "easeOut" }}
        />
      </div>
      {usage.fetchedAt > 0 && (
        <div className="flex items-center gap-1 text-[10px] text-muted-foreground">
          <Clock className="h-3 w-3" />
          Last updated: {new Date(usage.fetchedAt * 1000).toLocaleTimeString()}
        </div>
      )}
    </div>
  );
}

function ApiKeyInput({
  provider,
  onSave,
  onDelete,
  hasKey,
}: {
  provider: ApiProvider;
  onSave: (key: string) => void;
  onDelete: () => void;
  hasKey: boolean;
}) {
  const [key, setKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [isEditing, setIsEditing] = useState(!hasKey);

  const handleSave = () => {
    if (key.trim()) {
      onSave(key.trim());
      setKey("");
      setIsEditing(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      handleSave();
    } else if (e.key === "Escape") {
      setKey("");
      setIsEditing(false);
    }
  };

  if (!isEditing && hasKey) {
    return (
      <div className="flex items-center gap-2">
        <div className="flex items-center gap-2 px-3 py-1.5 bg-green-500/10 text-green-600 dark:text-green-400 rounded-lg text-sm">
          <CheckCircle2 className="h-4 w-4" />
          <span>API Key configured</span>
        </div>
        <motion.div whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.95 }}>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setIsEditing(true)}
          >
            Update
          </Button>
        </motion.div>
        <motion.div whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.95 }}>
          <Button
            variant="outline"
            size="sm"
            onClick={onDelete}
            className="text-red-500 hover:text-red-600"
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </motion.div>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <div className="relative flex-1">
          <input
            type={showKey ? "text" : "password"}
            value={key}
            onChange={(e) => setKey(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={`Enter ${provider.name} API key`}
            className="w-full px-3 py-2 pr-10 border rounded-lg text-sm bg-background text-foreground focus:ring-2 focus:ring-primary focus:border-transparent"
            autoFocus
          />
          <button
            type="button"
            onClick={() => setShowKey(!showKey)}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
          >
            {showKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
          </button>
        </div>
        <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
          <Button onClick={handleSave} disabled={!key.trim()}>
            Save
          </Button>
        </motion.div>
        {hasKey && (
          <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
            <Button variant="outline" onClick={() => setIsEditing(false)}>
              Cancel
            </Button>
          </motion.div>
        )}
      </div>
    </div>
  );
}

function OAuthStatus({ provider }: { provider: ApiProvider }) {
  const expiresText = provider.expiresAt
    ? new Date(provider.expiresAt).toLocaleString()
    : null;

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3">
        {provider.status === "configured" ? (
          <div className="flex items-center gap-2 px-3 py-1.5 bg-green-500/10 text-green-600 dark:text-green-400 rounded-lg text-sm">
            <CheckCircle2 className="h-4 w-4" />
            <span>Connected</span>
          </div>
        ) : provider.status === "expired" ? (
          <div className="flex items-center gap-2 px-3 py-1.5 bg-yellow-500/10 text-yellow-600 dark:text-yellow-400 rounded-lg text-sm">
            <AlertCircle className="h-4 w-4" />
            <span>Token expired</span>
          </div>
        ) : (
          <div className="flex items-center gap-2 px-3 py-1.5 bg-muted text-muted-foreground rounded-lg text-sm">
            <Key className="h-4 w-4" />
            <span>Not connected</span>
          </div>
        )}
      </div>
      {provider.email && (
        <p className="text-sm text-muted-foreground">Account: {provider.email}</p>
      )}
      {expiresText && (
        <p className="text-xs text-muted-foreground">
          Expires: {expiresText}
        </p>
      )}
      <p className="text-xs text-muted-foreground">
        OAuth authentication is managed through OpenClaw CLI
      </p>
    </div>
  );
}

export function ApiConfig() {
  const {
    apiProviders,
    apiUsage,
    modelAvailabilityReport,
    fetchApiProviders,
    fetchApiUsage,
    fetchAllApiUsage,
    fetchModelAvailabilityReport,
    setApiKey,
    deleteApiKey,
  } = useAppStore();
  const [isRefreshing, setIsRefreshing] = useState(false);

  // Initial fetch
  useEffect(() => {
    fetchApiProviders().then(() => fetchAllApiUsage());
    fetchModelAvailabilityReport();
  }, [fetchApiProviders, fetchAllApiUsage, fetchModelAvailabilityReport]);

  // Auto-refresh usage every minute when page is visible
  const refreshUsage = useCallback(() => {
    if (document.visibilityState === "visible") {
      fetchAllApiUsage();
    }
  }, [fetchAllApiUsage]);

  useEffect(() => {
    const interval = setInterval(refreshUsage, USAGE_REFRESH_INTERVAL);
    return () => clearInterval(interval);
  }, [refreshUsage]);

  const handleRefresh = async () => {
    if (isRefreshing) return;
    setIsRefreshing(true);
    await fetchApiProviders();
    await fetchModelAvailabilityReport();
    await fetchAllApiUsage();
    setIsRefreshing(false);
  };

  const handleRefreshProvider = async (providerId: string) => {
    await fetchApiUsage(providerId);
  };

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold">API Configuration</h2>
          <p className="text-muted-foreground">
            Manage API keys and view usage for AI providers
          </p>
        </div>
        <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
          <Button
            variant="outline"
            onClick={handleRefresh}
            disabled={isRefreshing}
          >
            <RefreshCw
              className={`h-4 w-4 mr-2 ${isRefreshing ? "animate-spin" : ""}`}
            />
            Refresh
          </Button>
        </motion.div>
      </div>

      {/* Runtime Availability */}
      <Card variant="glass">
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between gap-3">
            <CardTitle className="text-base flex items-center gap-2">
              <Zap className="h-4 w-4 text-primary" />
              Runtime Availability
            </CardTitle>
            {modelAvailabilityReport?.fetched_at ? (
              <div className="text-[10px] text-muted-foreground">
                Updated: {new Date(modelAvailabilityReport.fetched_at).toLocaleString()}
              </div>
            ) : null}
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 lg:grid-cols-3">
            <AgentAvailabilityCard
              title="translator-core"
              agent={modelAvailabilityReport?.agents?.["translator-core"]}
            />
            <AgentAvailabilityCard
              title="review-core"
              agent={modelAvailabilityReport?.agents?.["review-core"]}
            />
            <Card variant="glass">
              <CardHeader className="pb-3">
                <CardTitle className="text-base flex items-center gap-2">
                  <Zap className="h-4 w-4 text-primary" />
                  Vision / GLM
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="text-sm font-medium">Vision QA credentials</div>
                  <Badge
                    variant={
                      modelAvailabilityReport?.vision?.has_google_api_key ||
                      modelAvailabilityReport?.vision?.has_gemini_api_key ||
                      modelAvailabilityReport?.vision?.has_moonshot_api_key ||
                      modelAvailabilityReport?.vision?.has_openai_api_key
                        ? "success"
                        : "warning"
                    }
                  >
                    {modelAvailabilityReport?.vision?.has_google_api_key ||
                    modelAvailabilityReport?.vision?.has_gemini_api_key ||
                    modelAvailabilityReport?.vision?.has_moonshot_api_key ||
                    modelAvailabilityReport?.vision?.has_openai_api_key
                      ? "Configured"
                      : "Missing"}
                  </Badge>
                </div>
                <div className="text-xs text-muted-foreground">
                  GOOGLE_API_KEY:{" "}
                  {modelAvailabilityReport?.vision?.has_google_api_key ? "set" : "missing"} • GEMINI_API_KEY:{" "}
                  {modelAvailabilityReport?.vision?.has_gemini_api_key ? "set" : "missing"} • Moonshot:{" "}
                  {modelAvailabilityReport?.vision?.has_moonshot_api_key ? "set" : "missing"} • OPENAI_API_KEY:{" "}
                  {modelAvailabilityReport?.vision?.has_openai_api_key ? "set" : "missing"}
                </div>
                {modelAvailabilityReport?.vision?.vision_backend && (
                  <div className="text-xs text-muted-foreground">
                    OPENCLAW_VISION_BACKEND:{" "}
                    <code className="text-xs bg-muted px-1 rounded">{modelAvailabilityReport.vision.vision_backend}</code>
                  </div>
                )}
                {modelAvailabilityReport?.vision?.vision_model && (
                  <div className="text-xs text-muted-foreground">
                    Vision model override:{" "}
                    <code className="text-xs bg-muted px-1 rounded">
                      {modelAvailabilityReport.vision.vision_model}
                    </code>
                  </div>
                )}

                <div className="pt-3 border-t space-y-2">
                  <div className="flex items-center justify-between gap-3">
                    <div className="text-sm font-medium">GLM</div>
                    <Badge
                      variant={
                        modelAvailabilityReport?.glm?.glm_enabled
                          ? (modelAvailabilityReport.glm.has_glm_api_key || modelAvailabilityReport.glm.has_zai_profile)
                            ? "success"
                            : "warning"
                          : "outline"
                      }
                    >
                      {modelAvailabilityReport?.glm?.glm_enabled ? "Enabled" : "Disabled"}
                    </Badge>
                  </div>
                  {modelAvailabilityReport?.glm?.glm_enabled && (
                    <div className="text-xs text-muted-foreground">
                      GLM_API_KEY: {modelAvailabilityReport.glm.has_glm_api_key ? "set" : "missing"} • zai profile:{" "}
                      {modelAvailabilityReport.glm.has_zai_profile ? "present" : "missing"}
                    </div>
                  )}
                  {!modelAvailabilityReport?.glm?.glm_enabled && (
                    <div className="text-xs text-muted-foreground">
                      OPENCLAW_GLM_ENABLED is not set to 1.
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>
          </div>
          <div className="text-xs text-muted-foreground">
            This is a fast status view (no live probe). For details run:{" "}
            <code className="text-xs bg-muted px-1 rounded">openclaw models status --agent translator-core --json</code>
          </div>
        </CardContent>
      </Card>

      {/* Providers Grid */}
      <div className="grid gap-4">
        <AnimatePresence>
          {apiProviders.map((provider, index) => (
            <motion.div
              key={provider.id}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -20 }}
              transition={{ delay: index * 0.1 }}
            >
              <Card variant="glass">
                <CardHeader className="pb-3">
                  <div className="flex items-center justify-between">
                    <CardTitle className="text-base flex items-center gap-2">
                      <Zap className="h-4 w-4 text-primary" />
                      {provider.name}
                    </CardTitle>
                    <div className="flex items-center gap-2">
                      {getStatusBadge(provider.status)}
                      <Badge variant="outline">
                        {getAuthTypeLabel(provider.authType)}
                      </Badge>
                    </div>
                  </div>
                </CardHeader>
                <CardContent className="space-y-4">
                  {/* Configuration Section */}
                  {provider.authType === "api_key" ? (
                    <ApiKeyInput
                      provider={provider}
                      onSave={(key) => setApiKey(provider.id, key)}
                      onDelete={() => deleteApiKey(provider.id)}
                      hasKey={provider.hasKey}
                    />
                  ) : provider.authType === "oauth" ? (
                    <OAuthStatus provider={provider} />
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      No configuration required
                    </p>
                  )}

                  {/* Usage Section */}
                  {provider.authType === "api_key" && provider.hasKey && (
                    <div className="pt-3 border-t">
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-sm font-medium">Usage</span>
                        <motion.button
                          whileHover={{ scale: 1.1 }}
                          whileTap={{ scale: 0.9 }}
                          onClick={() => handleRefreshProvider(provider.id)}
                          className="text-muted-foreground hover:text-foreground"
                        >
                          <RefreshCw className="h-3.5 w-3.5" />
                        </motion.button>
                      </div>
                      <UsageBar usage={apiUsage[provider.id]} />
                    </div>
                  )}
                </CardContent>
              </Card>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>

      {/* Info Card */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: apiProviders.length * 0.1 }}
      >
        <Card className="border-blue-500/50 bg-blue-500/5">
          <CardContent className="flex items-start gap-3 p-4">
            <AlertCircle className="h-5 w-5 text-blue-500 mt-0.5" />
            <div>
              <p className="font-medium text-sm">About API Keys</p>
              <p className="text-xs text-muted-foreground mt-1">
                API keys are stored locally in <code className="text-xs bg-muted px-1 rounded">~/.openclaw/agents/main/agent/auth-profiles.json</code> and are never transmitted to external servers except when making API calls.
              </p>
            </div>
          </CardContent>
        </Card>
      </motion.div>
    </div>
  );
}
