import { useEffect } from "react";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/stores/appStore";
import {
  LayoutDashboard,
  Server,
  Briefcase,
  FileCheck,
  ScrollText,
  Settings,
  Database,
  ChevronLeft,
  Sun,
  Moon,
  Monitor,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";

const navItems = [
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { id: "services", label: "Services", icon: Server },
  { id: "jobs", label: "Jobs", icon: Briefcase },
  { id: "verify", label: "Verify", icon: FileCheck },
  { id: "logs", label: "Logs", icon: ScrollText },
  { id: "kb-health", label: "KB Health", icon: Database },
  { id: "settings", label: "Settings", icon: Settings },
];

const themeOptions: { value: "light" | "dark" | "system"; icon: typeof Sun; label: string }[] = [
  { value: "light", icon: Sun, label: "Light" },
  { value: "dark", icon: Moon, label: "Dark" },
  { value: "system", icon: Monitor, label: "System" },
];

export function Sidebar() {
  const { activeTab, setActiveTab, theme, setTheme, sidebarCollapsed, setSidebarCollapsed, services, fetchServices } = useAppStore();

  // Poll service status every 15s
  useEffect(() => {
    fetchServices();
    const interval = setInterval(fetchServices, 15000);
    return () => clearInterval(interval);
  }, [fetchServices]);

  const currentThemeOption = themeOptions.find((o) => o.value === theme) || themeOptions[2];
  const ThemeIcon = currentThemeOption.icon;

  // Cycle through themes: light → dark → system → light
  const cycleTheme = () => {
    const currentIndex = themeOptions.findIndex((o) => o.value === theme);
    const nextIndex = (currentIndex + 1) % themeOptions.length;
    setTheme(themeOptions[nextIndex].value);
  };

  // Compute aggregate status
  const allRunning = services.every((s) => s.status === "running");
  const anyRunning = services.some((s) => s.status === "running");
  const statusColor = allRunning ? "bg-green-500" : anyRunning ? "bg-yellow-500" : "bg-red-500";
  const statusGlow = allRunning ? "glow-green" : anyRunning ? "glow-yellow" : "glow-red";
  const statusLabel = allRunning ? "All Running" : anyRunning ? "Partial" : "Stopped";

  return (
    <aside
      className={cn(
        "sidebar-vibrancy flex flex-col h-screen transition-all duration-300 ease-in-out",
        "border-r border-border/50",
        sidebarCollapsed ? "w-16" : "w-56"
      )}
    >
      {/* Header */}
      <div className="p-3 flex items-center justify-between border-b border-border/50">
        <AnimatePresence mode="wait">
          {!sidebarCollapsed && (
            <motion.div
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -10 }}
              transition={{ duration: 0.2 }}
              className="flex-1 min-w-0"
            >
              <h1 className="font-semibold text-sm truncate">Inifity</h1>
              <p className="text-[10px] text-muted-foreground">v1.0.0</p>
            </motion.div>
          )}
        </AnimatePresence>
        <motion.button
          onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
          whileHover={{ scale: 1.1 }}
          whileTap={{ scale: 0.9 }}
          className={cn(
            "p-1.5 rounded-lg hover:bg-muted/50 transition-colors",
            sidebarCollapsed && "mx-auto"
          )}
        >
          <ChevronLeft
            className={cn(
              "h-4 w-4 text-muted-foreground transition-transform duration-300",
              sidebarCollapsed && "rotate-180"
            )}
          />
        </motion.button>
      </div>

      {/* Navigation - Pill buttons */}
      <nav className="flex-1 p-2 space-y-1 overflow-y-auto overflow-x-hidden">
        {navItems.map((item) => (
          <motion.button
            key={item.id}
            onClick={() => setActiveTab(item.id)}
            whileHover={{ scale: 1.02, x: sidebarCollapsed ? 0 : 2 }}
            whileTap={{ scale: 0.98 }}
            className={cn(
              "w-full flex items-center gap-3 px-3 py-2 rounded-full",
              "transition-all duration-200 ease-out",
              "active:scale-[0.96]",
              activeTab === item.id
                ? "bg-primary text-primary-foreground shadow-md"
                : "hover:bg-muted/50 text-muted-foreground hover:text-foreground"
            )}
          >
            <item.icon className="h-4 w-4 flex-shrink-0" />
            <AnimatePresence mode="wait">
              {!sidebarCollapsed && (
                <motion.span
                  initial={{ opacity: 0, width: 0 }}
                  animate={{ opacity: 1, width: "auto" }}
                  exit={{ opacity: 0, width: 0 }}
                  transition={{ duration: 0.2 }}
                  className="text-sm font-medium whitespace-nowrap overflow-hidden"
                >
                  {item.label}
                </motion.span>
              )}
            </AnimatePresence>
          </motion.button>
        ))}
      </nav>

      {/* Footer with theme toggle */}
      <div className="p-3 border-t border-border/50">
        <motion.button
          onClick={cycleTheme}
          whileHover={{ scale: 1.05 }}
          whileTap={{ scale: 0.95 }}
          className={cn(
            "w-full flex items-center gap-3 px-3 py-2 rounded-full",
            "hover:bg-muted/50 transition-colors",
            "text-muted-foreground hover:text-foreground"
          )}
        >
          <ThemeIcon className="h-4 w-4 flex-shrink-0" />
          <AnimatePresence mode="wait">
            {!sidebarCollapsed && (
              <motion.span
                key={theme}
                initial={{ opacity: 0, y: 5 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -5 }}
                transition={{ duration: 0.15 }}
                className="text-sm font-medium whitespace-nowrap overflow-hidden"
              >
                {currentThemeOption.label}
              </motion.span>
            )}
          </AnimatePresence>
        </motion.button>

        {/* Dynamic status indicator */}
        <div className={cn(
          "flex items-center gap-2 mt-2 px-3 py-1.5",
          sidebarCollapsed && "justify-center"
        )}>
          <motion.div
            className={cn("h-2 w-2 rounded-full", statusColor, statusGlow)}
            animate={{ scale: [1, 1.2, 1], opacity: [1, 0.7, 1] }}
            transition={{ duration: 2, repeat: Infinity }}
          />
          <AnimatePresence mode="wait">
            {!sidebarCollapsed && (
              <motion.span
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="text-[10px] text-muted-foreground"
              >
                {statusLabel}
              </motion.span>
            )}
          </AnimatePresence>
        </div>
      </div>
    </aside>
  );
}
