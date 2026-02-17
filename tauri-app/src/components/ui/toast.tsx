import { useEffect, useState, useRef } from "react";
import { motion, AnimatePresence, useMotionValue, useTransform, PanInfo } from "framer-motion";
import { CheckCircle2, AlertCircle, AlertTriangle, Info } from "lucide-react";
import { cn } from "@/lib/utils";

export type ToastType = "success" | "error" | "warning" | "info";

export interface ToastItem {
  id: string;
  type: ToastType;
  message: string;
}

const iconMap = {
  success: CheckCircle2,
  error: AlertCircle,
  warning: AlertTriangle,
  info: Info,
};

const styleMap = {
  success: "border-green-500/40 bg-green-950/80 text-green-300",
  error: "border-red-500/40 bg-red-950/80 text-red-300",
  warning: "border-yellow-500/40 bg-yellow-950/80 text-yellow-300",
  info: "border-primary/40 bg-card/90 text-primary",
};

const glowMap = {
  success: "shadow-[0_0_20px_rgba(34,197,94,0.15)]",
  error: "shadow-[0_0_20px_rgba(239,68,68,0.15)]",
  warning: "shadow-[0_0_20px_rgba(234,179,8,0.15)]",
  info: "shadow-[0_0_20px_rgba(20,184,166,0.15)]",
};

const autoDismissMs = {
  success: 4000,
  info: 5000,
  warning: 10000,
  error: 15000,
};

/* ── Confirm Modal ── */
function ConfirmModal({
  onConfirm,
  onCancel,
}: {
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <motion.div
      className="fixed inset-0 z-[99999] flex items-center justify-center"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
    >
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onMouseDown={onCancel} />
      <motion.div
        initial={{ scale: 0.9, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.9, opacity: 0 }}
        transition={{ type: "spring", stiffness: 400, damping: 25 }}
        className="relative z-10 w-80 rounded-2xl border border-border/60 bg-card/95 backdrop-blur-xl p-5 shadow-2xl"
      >
        <h3 className="text-sm font-semibold mb-1">Dismiss Alert</h3>
        <p className="text-xs text-muted-foreground mb-4">
          Are you sure you want to dismiss this notification?
        </p>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onMouseDown={(e) => { e.stopPropagation(); onCancel(); }}
            className="px-4 py-1.5 text-xs rounded-xl border border-border bg-background hover:bg-muted transition-colors cursor-pointer"
          >
            Cancel
          </button>
          <button
            type="button"
            onMouseDown={(e) => { e.stopPropagation(); onConfirm(); }}
            className="px-4 py-1.5 text-xs rounded-xl bg-red-600 hover:bg-red-500 text-white font-medium transition-colors cursor-pointer"
          >
            Dismiss
          </button>
        </div>
      </motion.div>
    </motion.div>
  );
}

/* ── Single Toast ── */
function ToastMessage({
  toast,
  onDismiss,
}: {
  toast: ToastItem;
  onDismiss: () => void;
}) {
  const Icon = iconMap[toast.type];
  const [showConfirm, setShowConfirm] = useState(false);
  const x = useMotionValue(0);
  const opacity = useTransform(x, [-120, -60, 0], [0.3, 0.8, 1]);
  const dismissed = useRef(false);

  // Auto-dismiss all toasts
  useEffect(() => {
    const ms = autoDismissMs[toast.type];
    const timer = setTimeout(onDismiss, ms);
    return () => clearTimeout(timer);
  }, [toast.type, onDismiss]);

  const handleDragEnd = (_: unknown, info: PanInfo) => {
    if (dismissed.current) return;
    // Swipe LEFT threshold
    if (info.offset.x < -80) {
      if (toast.type === "error" || toast.type === "warning") {
        x.set(0);
        setShowConfirm(true);
      } else {
        dismissed.current = true;
        onDismiss();
      }
    }
  };

  const handleConfirm = () => {
    dismissed.current = true;
    setShowConfirm(false);
    onDismiss();
  };

  return (
    <>
      <motion.div
        style={{ x, opacity }}
        drag="x"
        dragConstraints={{ left: 0, right: 0 }}
        dragElastic={{ left: 0.4, right: 0 }}
        onDragEnd={handleDragEnd}
        initial={{ opacity: 0, x: 80, scale: 0.9 }}
        animate={{ opacity: 1, x: 0, scale: 1 }}
        exit={{ opacity: 0, x: -200, scale: 0.8 }}
        transition={{ duration: 0.3, ease: "easeOut" }}
        className={cn(
          "flex items-start gap-3 p-4 rounded-2xl border backdrop-blur-xl max-w-sm cursor-grab active:cursor-grabbing select-none",
          styleMap[toast.type],
          glowMap[toast.type]
        )}
      >
        <Icon className="h-5 w-5 shrink-0 mt-0.5" />
        <p className="text-sm flex-1 leading-relaxed">{toast.message}</p>
      </motion.div>

      <AnimatePresence>
        {showConfirm && (
          <ConfirmModal
            onConfirm={handleConfirm}
            onCancel={() => setShowConfirm(false)}
          />
        )}
      </AnimatePresence>
    </>
  );
}

/* ── Container ── */
export function ToastContainer({
  toasts,
  onDismiss,
}: {
  toasts: ToastItem[];
  onDismiss: (id: string) => void;
}) {
  return (
    <div className="fixed bottom-6 right-6 z-[9999] flex flex-col-reverse gap-2">
      <AnimatePresence mode="popLayout">
        {toasts.map((toast) => (
          <div key={toast.id}>
            <ToastMessage toast={toast} onDismiss={() => onDismiss(toast.id)} />
          </div>
        ))}
      </AnimatePresence>
    </div>
  );
}
