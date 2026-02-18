import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useAppStore, ApiProvider, ApiUsage } from "@/stores/appStore";
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
    fetchApiProviders,
    fetchApiUsage,
    fetchAllApiUsage,
    setApiKey,
    deleteApiKey,
  } = useAppStore();
  const [isRefreshing, setIsRefreshing] = useState(false);

  // Initial fetch
  useEffect(() => {
    fetchApiProviders().then(() => {
      fetchAllApiUsage();
    });
  }, [fetchApiProviders, fetchAllApiUsage]);

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
