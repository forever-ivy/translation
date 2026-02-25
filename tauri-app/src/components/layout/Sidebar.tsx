import { cn } from "@/lib/utils";
import { useUiStore } from "@/stores/uiStore";
import { useServiceStore } from "@/stores/serviceStore";
import { APP_ROUTE_PATHS } from "@/shared/routes";
import {
  Briefcase,
  FileCheck,
  ScrollText,
  Settings,
  Database,
  BookText,
  ChevronLeft,
  Sun,
  Moon,
  Monitor,
  Rocket,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { NavLink, useLocation } from "react-router-dom";

const navItems = [
  { route: "start-openclaw", label: "Runtime", icon: Rocket },
  { route: "jobs", label: "Jobs", icon: Briefcase },
  { route: "verify", label: "Verify", icon: FileCheck },
  { route: "logs", label: "Logs", icon: ScrollText },
  { route: "kb-health", label: "KB Health", icon: Database },
  { route: "glossary", label: "Glossary", icon: BookText },
  { route: "settings", label: "Settings", icon: Settings },
] as const;

const themeOptions: { value: "light" | "dark" | "system"; icon: typeof Sun; label: string }[] = [
  { value: "light", icon: Sun, label: "Light" },
  { value: "dark", icon: Moon, label: "Dark" },
  { value: "system", icon: Monitor, label: "System" },
];

export function Sidebar() {
  const location = useLocation();
  const theme = useUiStore((s) => s.theme);
  const setTheme = useUiStore((s) => s.setTheme);
  const sidebarCollapsed = useUiStore((s) => s.sidebarCollapsed);
  const setSidebarCollapsed = useUiStore((s) => s.setSidebarCollapsed);
  const services = useServiceStore((s) => s.services);

  const currentThemeOption = themeOptions.find((o) => o.value === theme) || themeOptions[2];
  const ThemeIcon = currentThemeOption.icon;

  const cycleTheme = () => {
    const currentIndex = themeOptions.findIndex((o) => o.value === theme);
    const nextIndex = (currentIndex + 1) % themeOptions.length;
    setTheme(themeOptions[nextIndex].value);
  };

  const allRunning = services.length > 0 && services.every((s) => s.status === "running");
  const anyRunning = services.some((s) => s.status === "running");
  const statusColor = allRunning ? "bg-green-500" : anyRunning ? "bg-yellow-500" : "bg-red-500";
  const statusGlow = allRunning ? "glow-green" : anyRunning ? "glow-yellow" : "glow-red";
  const statusLabel = allRunning ? "All Running" : anyRunning ? "Partial" : "Stopped";

  return (
    <aside
      className={cn(
        "sidebar-vibrancy flex flex-col h-screen transition-all duration-300 ease-in-out",
        "border-r border-border/50",
        sidebarCollapsed ? "w-16" : "w-56",
      )}
    >
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
          aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
          type="button"
          whileHover={{ scale: 1.1 }}
          whileTap={{ scale: 0.9 }}
          className={cn("p-1.5 rounded-lg hover:bg-muted/50 transition-colors", sidebarCollapsed && "mx-auto")}
        >
          <ChevronLeft
            className={cn(
              "h-4 w-4 text-muted-foreground transition-transform duration-300",
              sidebarCollapsed && "rotate-180",
            )}
          />
        </motion.button>
      </div>

      <nav className="flex-1 p-2 space-y-1 overflow-y-auto overflow-x-hidden">
        {navItems.map((item) => {
          const path = APP_ROUTE_PATHS[item.route];
          const isActive = location.pathname === path || location.pathname.startsWith(`${path}/`);

          return (
            <NavLink key={item.route} to={path} aria-current={isActive ? "page" : undefined} aria-label={item.label}>
              <motion.div
                whileHover={{ scale: 1.02, x: sidebarCollapsed ? 0 : 2 }}
                whileTap={{ scale: 0.98 }}
                className={cn(
                  "w-full flex items-center gap-3 px-3 py-2 rounded-full",
                  "transition-all duration-200 ease-out",
                  "active:scale-[0.96]",
                  isActive
                    ? "bg-primary text-primary-foreground shadow-md"
                    : "hover:bg-muted/50 text-muted-foreground hover:text-foreground",
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
              </motion.div>
            </NavLink>
          );
        })}
      </nav>

      <div className="p-3 border-t border-border/50">
        <motion.button
          onClick={cycleTheme}
          aria-label={`Switch theme (current: ${currentThemeOption.label})`}
          type="button"
          whileHover={{ scale: 1.05 }}
          whileTap={{ scale: 0.95 }}
          className={cn(
            "w-full flex items-center gap-3 px-3 py-2 rounded-full",
            "hover:bg-muted/50 transition-colors",
            "text-muted-foreground hover:text-foreground",
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

        <div className={cn("flex items-center gap-2 mt-2 px-3 py-1.5", sidebarCollapsed && "justify-center")}>
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
